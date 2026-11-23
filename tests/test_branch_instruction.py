"""D306 pt 4: a fork may carry the branch's FIRST INSTRUCTION, so starting a branch is one act —
a fork used to create the branch as a finished run and navigate to it, leaving the person to type
its first instruction there, while `ForkBody.instruction` files that text through the same
inbox + wake path `POST /conversations/{slug}/message` uses, so the branch is already working when
they arrive; an empty instruction keeps the old behaviour exactly (the branch opens idle). Its own
module because tests/test_branches.py is 18 lines under the 350-line house bar.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import authed_client, seeded_server
from rsched import conversations as conv_mod
from rsched.engine import inbox
from rsched.paths import atomic_write_json

#: header, two complete turns, a finish — turn 1 is the fork point throughout.
EVENTS = [
    {"type": "header", "run_id": "c-p:20260827-100000", "routine": "c-p",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 900, "out": 40},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "finish", "turn": 1, "payload": {"status": "ok", "summary": "weighed both"}},
]


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def client(server):
    """The API over `server`, with the wake RECORDED instead of spawning an engine: a fork with
    an instruction resumes the branch, and what the route must prove is that it asked for that
    resume on the branch's own config."""
    with authed_client(server) as c:
        woken: list[tuple[str, str]] = []

        async def fake_resume_terminal(cfg, ts=None, *, reason="resume"):
            woken.append((cfg.slug, reason))
            return f"{cfg.slug}:20260827-120000"

        c.app.state.runner.resume_terminal = fake_resume_terminal
        c.app.state.runner.woken = woken
        yield c


def _parent(server, *, slug="c-p"):
    d = conv_mod.create_conversation(server, slug=slug, first_message="do the thing")
    run_dir = d / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 1})
    return d


def test_a_fork_with_an_instruction_files_it_and_starts_the_branch(server, client):
    """The instruction rides the same channel a typed message does (`via="conversation"`), and
    the branch is woken through `resume_terminal` — the path that REPLAYS the inherited
    transcript. A fresh fire would throw that history away."""
    _parent(server)
    r = client.post("/api/conversations/c-p/branch",
                    json={"turn": 1, "name": "Cost curve",
                          "instruction": "Cost out option B over three years."})
    assert r.status_code == 200, r.text
    body = r.json()
    slug = body["slug"]
    assert body["started"] is True
    assert body["run_id"].startswith(f"{slug}:")

    msgs = list((server.conversations_home / slug / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1, msgs
    record = json.loads(msgs[0].read_text(encoding="utf-8"))
    assert record["text"] == "Cost out option B over three years."
    assert record["via"] == "conversation"
    assert inbox.user_authored(record["via"])      # the operator's own click started this
    # the wake is the branch's, asked for on the branch's own slug
    assert client.app.state.runner.woken == [(slug, "converse")]


def test_a_fork_without_an_instruction_leaves_the_branch_idle(server, client):
    """Today's behaviour, kept exactly: nothing queued, nothing woken."""
    _parent(server)
    r = client.post("/api/conversations/c-p/branch", json={"turn": 1})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is False
    slug = r.json()["slug"]
    assert list((server.conversations_home / slug / "inbox").glob("msg-*.json")) == []
    assert client.app.state.runner.woken == []


def test_a_whitespace_only_instruction_is_no_instruction(server, client):
    """A textarea the person tabbed through holds a newline, not an intention — and an empty
    message is what the message route itself refuses."""
    _parent(server)
    r = client.post("/api/conversations/c-p/branch",
                    json={"turn": 1, "instruction": "  \n \t "})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is False
    slug = r.json()["slug"]
    assert list((server.conversations_home / slug / "inbox").glob("msg-*.json")) == []
    assert client.app.state.runner.woken == []


def test_the_instruction_does_not_disturb_the_fork_itself(server, client):
    """The branch is still a branch: its own slug, the parent recorded, the history copied
    through the fork point and no further."""
    d = _parent(server)
    slug = client.post("/api/conversations/c-p/branch",
                       json={"turn": 1, "instruction": "go"}).json()["slug"]
    b = server.conversations_home / slug
    raw = yaml.safe_load((b / "routine.yaml").read_text())
    assert raw["slug"] == slug and raw["parent"] == {**raw["parent"], "slug": "c-p", "turn": 1}
    evs = [json.loads(x) for x in
           (next((b / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    assert [e["type"] for e in evs] == ["header", "assistant_action", "observation"]
    # the parent neither receives the instruction nor is woken for it
    assert list((d / "inbox").glob("msg-*.json")) == []


def test_a_fork_with_an_instruction_is_refused_while_the_server_is_restarting(server, client):
    """R81, the branch door: in the restart's exit window the wake is refused. Forking first
    would leave a branch in the list holding an instruction nobody is reading, and the retry
    would make a second one — so the refusal comes BEFORE the fork."""
    _parent(server)
    client.app.state.runner.draining = True
    try:
        r = client.post("/api/conversations/c-p/branch",
                        json={"turn": 1, "instruction": "go"})
    finally:
        client.app.state.runner.draining = False
    assert r.status_code == 503 and "restarting" in r.json()["detail"]
    assert not (server.conversations_home / "c-p-b1").exists()
    assert client.app.state.runner.woken == []


def test_a_fork_without_an_instruction_still_works_while_restarting(server, client):
    """Nothing has to be woken, so there is nothing the drain can strand: a plain fork is a
    file copy and stays available."""
    _parent(server)
    client.app.state.runner.draining = True
    try:
        r = client.post("/api/conversations/c-p/branch", json={"turn": 1})
    finally:
        client.app.state.runner.draining = False
    assert r.status_code == 200, r.text
    assert r.json()["started"] is False


def test_a_wake_that_does_not_take_leaves_the_instruction_queued(server, client):
    """A refused wake is not a lost instruction: it stays in the branch's inbox and its next
    reply drains it. The response says `started: false` rather than claim a start."""
    _parent(server)

    async def no_wake(cfg, ts=None, *, reason="resume"):
        return None

    client.app.state.runner.resume_terminal = no_wake
    r = client.post("/api/conversations/c-p/branch",
                    json={"turn": 1, "instruction": "go"})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is False and "run_id" not in r.json()
    slug = r.json()["slug"]
    msgs = list((server.conversations_home / slug / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1, msgs
