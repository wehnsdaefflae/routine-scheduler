"""Conversation branching (F325): fork at a turn, and hand a branch's result back.

The two invariants these exist for: a branch **cannot mutate the original** (that is the whole
point of copying rather than sharing), and merging is a **hand-back, never a transcript merge**
(two divergent histories cannot be interleaved into one coherent conversation).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched import branches
from rsched import conversations as conv_mod
from rsched.paths import atomic_write_json
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"
TOKEN = "test-token"

# A tiny but REAL transcript: header, two complete turns, a finish. Turn 1 is the fork point
# used throughout — everything after it belongs to the parent alone.
EVENTS = [
    {"type": "header", "run_id": "c-p:20260827-100000", "routine": "c-p",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 900, "out": 40},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "assistant_action", "turn": 2, "usage": {"in": 1200, "out": 60},
     "payload": {"kind": "write_file", "say": "AFTER-THE-FORK", "path": "state/late.md"}},
    {"type": "observation", "turn": 2, "payload": {"kind": "write_file", "ok": True}},
    {"type": "finish", "turn": 2, "payload": {"status": "ok", "summary": "parent went on"}},
]


@pytest.fixture
def server(tmp_path):
    lib = tmp_path / "library"
    shutil.copytree(SEED / "workflows", lib / "workflows")
    shutil.copytree(SEED / "rules", lib / "rules")
    shutil.copytree(SEED / "permissions", lib / "permissions")
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            libraries_home=str(lib))


def _parent(server, *, slug="c-p"):
    """A conversation with one finished run whose transcript is EVENTS, plus the on-disk
    furniture a fork must carry across."""
    d = conv_mod.create_conversation(server, slug=slug, first_message="do the thing")
    run_dir = d / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})
    (d / "state" / "plan.md").write_text("PLAN", encoding="utf-8")
    (d / "attachments" / "shot.png").write_bytes(b"PNG")
    (d / "artifacts" / "parents-own.md").write_text("the parent's deliverable", encoding="utf-8")
    return d


def test_fork_copies_history_to_the_fork_point_and_no_further(server):
    d = _parent(server)
    made = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    bdir = server.conversations_home / made["slug"]
    evs = [json.loads(x) for x in
           (next((bdir / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    # header + turn 1's action and its observation. The cut snaps to a whole turn: an
    # assistant action without the result it saw would replay as a dangling turn.
    assert [e["type"] for e in evs] == ["header", "assistant_action", "observation"]
    assert not any("AFTER-THE-FORK" in json.dumps(e) for e in evs)
    # the header names the BRANCH — a copied one would have its transcript claiming to be the
    # parent's run, which every read model keys off
    assert evs[0]["routine"] == made["slug"]
    assert evs[0]["run_id"].startswith(made["slug"] + ":")
    assert evs[0]["branched_from"] == {"slug": "c-p", "turn": 1}


def test_fork_strips_usage_so_spend_is_not_counted_twice(server):
    """The parent already accounted for the inherited history's spend; the branch's meters
    must report what the BRANCH cost."""
    d = _parent(server)
    made = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    bdir = server.conversations_home / made["slug"]
    evs = [json.loads(x) for x in
           (next((bdir / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    assert all("usage" not in e for e in evs)
    # ...but the content of what was said is untouched
    assert evs[1]["payload"]["say"] == "orienting"


def test_fork_inherits_config_and_records_its_parent(server):
    d = _parent(server)
    raw_parent = yaml.safe_load((d / "routine.yaml").read_text())
    made = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    raw = yaml.safe_load((server.conversations_home / made["slug"] / "routine.yaml").read_text())
    assert raw["parent"]["slug"] == "c-p" and raw["parent"]["turn"] == 1
    for key in ("kind", "models", "permissions", "rules", "budgets", "capabilities",
                "fs_read_roots", "fs_write_roots"):
        if key in raw_parent:
            assert raw[key] == raw_parent[key], f"{key} did not carry over"
    assert raw["name"] != raw_parent["name"]        # it is a branch, and says so


def test_fork_gets_its_own_slug_not_the_parents(server):
    """The branch must load under its OWN slug (its dir name), never the parent's. The parent's
    routine.yaml we copy carries the parent slug; if the fork keeps it, load_routine reads that
    slug (raw["slug"] wins over the dir name) and the branch runs under the PARENT's slug — a
    collision in the runner's slug-keyed active map that wedges both conversations (the parent
    unreachable; a message to the branch refused as an overrun)."""
    from rsched.config import load_routine

    d = _parent(server)
    made = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    raw = yaml.safe_load((server.conversations_home / made["slug"] / "routine.yaml").read_text())
    assert raw["slug"] == made["slug"] != "c-p"          # its own dir slug, not the parent's
    cfg, problems = load_routine(server.conversations_home / made["slug"])
    assert cfg.slug == made["slug"]                       # loads under its own slug
    assert not any("does not match directory name" in p for p in problems)


def test_fork_carries_the_files_its_history_refers_to_but_not_artifacts(server):
    """A transcript mentioning attachments/shot.png with no such file is a broken history.
    artifacts/ is the exception: the branch produces its OWN and hands those back — copying
    the parent's would make every hand-back return the parent its own files."""
    d = _parent(server)
    b = server.conversations_home / branches.fork_conversation(
        server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    assert (b / "state" / "plan.md").read_text() == "PLAN"
    assert (b / "attachments" / "shot.png").read_bytes() == b"PNG"
    assert (b / "main.md").is_file() and (b / "instruction.md").is_file()
    assert (b / "artifacts").is_dir() and not any((b / "artifacts").iterdir())


def test_branch_cannot_mutate_the_original(server):
    """The reason a fork COPIES. Writing all over the branch leaves the parent byte-identical."""
    d = _parent(server)
    before = {p.relative_to(d): p.read_bytes()
              for p in d.rglob("*") if p.is_file()}
    b = server.conversations_home / branches.fork_conversation(
        server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    (b / "state" / "plan.md").write_text("REWRITTEN BY THE BRANCH", encoding="utf-8")
    (b / "runs" / next((b / "runs").iterdir()).name / "transcript.jsonl").write_text(
        "{}\n", encoding="utf-8")
    (b / "attachments" / "shot.png").write_bytes(b"CLOBBERED")
    after = {p.relative_to(d): p.read_bytes() for p in d.rglob("*") if p.is_file()}
    assert after == before


def test_fork_is_resumable_as_an_ordinary_terminal_run(server):
    """A branch must be a CONTINUED conversation from turn one, not a special case in the
    engine: its first message goes down the ordinary resume_terminal path, which needs a
    terminal status.json beside the copied transcript."""
    from rsched import registry

    d = _parent(server)
    b = server.conversations_home / branches.fork_conversation(
        server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    runs = registry.run_index(b, b.name)
    assert len(runs) == 1
    assert runs[0].state in registry.TERMINAL_STATES
    assert runs[0].turn == 1


def test_fork_rejects_a_turn_that_is_not_there(server):
    d = _parent(server)
    with pytest.raises(ValueError, match="turn 9"):
        branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=9)


def test_second_branch_gets_its_own_slug(server):
    d = _parent(server)
    a = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    c = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    assert a != c and a.startswith("c-p-b") and c.startswith("c-p-b")


# ---- the hand-back -------------------------------------------------------------------------

def test_handback_delivers_summary_and_artifacts_without_merging(server):
    """Merging is deliberately a HAND-BACK: the parent gets a message and files, and its own
    transcript is untouched — two divergent histories are never interleaved."""
    d = _parent(server)
    slug = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    b = server.conversations_home / slug
    (b / "artifacts" / "result.md").write_text("what the branch found", encoding="utf-8")

    out = branches.hand_back(server, branch_dir=b, slug=slug, summary="the branch concluded X")
    assert out["parent"] == "c-p" and out["copied"] == 1
    landed = d / "artifacts" / f"{branches.HANDBACK_PREFIX}{slug}" / "result.md"
    assert landed.read_text() == "what the branch found"
    assert (d / "artifacts" / "parents-own.md").is_file()      # never clobbers the parent's own

    msgs = list((d / "inbox").glob("msg-branch-*.json"))
    assert len(msgs) == 1
    text = json.loads(msgs[0].read_text())["text"]
    assert "the branch concluded X" in text and slug in text
    assert "not a merge" in text
    # the parent's transcript is untouched — the hand-back is a delivery, not a rewrite
    tp = d / "runs" / "20260827-100000" / "transcript.jsonl"
    assert [json.loads(x)["type"] for x in tp.read_text().splitlines()] == [
        e["type"] for e in EVENTS]


def test_handback_is_idempotent_and_survives_a_second_pass(server):
    d = _parent(server)
    slug = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    b = server.conversations_home / slug
    (b / "artifacts" / "result.md").write_text("v1", encoding="utf-8")
    branches.hand_back(server, branch_dir=b, slug=slug, summary="first pass")
    (b / "artifacts" / "result.md").write_text("v2", encoding="utf-8")
    branches.hand_back(server, branch_dir=b, slug=slug, summary="second pass")
    assert (d / "artifacts" / f"{branches.HANDBACK_PREFIX}{slug}"
            / "result.md").read_text() == "v2"


def test_handback_refuses_from_a_root_conversation(server):
    d = _parent(server)
    with pytest.raises(ValueError, match="not a branch"):
        branches.hand_back(server, branch_dir=d, slug="c-p", summary="x")


def test_handback_refuses_when_the_parent_is_gone(server):
    d = _parent(server)
    slug = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)["slug"]
    shutil.rmtree(d)
    with pytest.raises(ValueError, match="no longer exists"):
        branches.hand_back(server, branch_dir=server.conversations_home / slug, slug=slug,
                           summary="x")


# ---- the API -------------------------------------------------------------------------------

@pytest.fixture
def client(server):
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_api_fork_handback_and_lineage(server, client):
    d = _parent(server)
    r = client.post("/api/conversations/c-p/branch", json={"turn": 1, "name": "Other path"})
    assert r.status_code == 200
    slug = r.json()["slug"]

    lin = client.get(f"/api/conversations/{slug}/lineage").json()
    assert lin["parent"]["slug"] == "c-p" and lin["parent"]["exists"] is True
    assert lin["parent"]["turn"] == 1
    # ...and the parent sees the branch, so a page can offer the way down as well as back
    back = client.get("/api/conversations/c-p/lineage").json()
    assert [b["slug"] for b in back["branches"]] == [slug]
    assert back["branches"][0]["name"] == "Other path"
    assert back["parent"] is None

    assert client.post(f"/api/conversations/{slug}/handback",
                       json={"summary": ""}).status_code == 400    # a summary IS the hand-back
    ok = client.post(f"/api/conversations/{slug}/handback", json={"summary": "done"})
    assert ok.status_code == 200 and ok.json()["parent"] == "c-p"
    assert list((d / "inbox").glob("msg-branch-*.json"))


def test_api_fork_refuses_mid_reply(server, client):
    """The fork point must be a settled turn — mid-reply the transcript is still growing."""
    d = _parent(server)
    atomic_write_json(d / "runs" / "20260827-100000" / "status.json",
                      {"state": "running", "turn": 2})
    r = client.post("/api/conversations/c-p/branch", json={"turn": 1})
    assert r.status_code == 409 and "mid-reply" in r.json()["detail"]


def test_api_lineage_reports_a_deleted_parent_instead_of_hiding_it(server, client):
    d = _parent(server)
    slug = client.post("/api/conversations/c-p/branch", json={"turn": 1}).json()["slug"]
    shutil.rmtree(d)
    lin = client.get(f"/api/conversations/{slug}/lineage").json()
    assert lin["parent"]["slug"] == "c-p" and lin["parent"]["exists"] is False
