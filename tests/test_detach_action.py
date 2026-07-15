"""The `detach` action: registration + gating, the structural root-conversation gate, the
intent write, and the detached-run ask-coercion (a detached task never blocks on the user)."""

import json
from types import SimpleNamespace

import yaml
from conftest import WORKFLOW_MD, finish

from rsched import grants
from rsched.config import ServerConfig
from rsched.engine import detach
from rsched.engine.actions import KINDS, validate_action
from rsched.engine.runtime import run_routine


def _ctx(tmp_path, *, home: str, slug="c-1", depth=0):
    server = SimpleNamespace(conversations_home=tmp_path / "conversations",
                             background_home=tmp_path / "background",
                             routines_home=tmp_path / "routines")
    routine = SimpleNamespace(slug=slug, dir=getattr(server, home) / slug)
    routine.dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(server=server, routine=routine, depth=depth)


def test_detach_registered_and_gated():
    assert "detach" in KINDS
    assert "detach" in grants.GATED_KINDS
    assert validate_action({"say": "s", "kind": "detach", "prompt": "do it"}) == []
    assert validate_action({"say": "s", "kind": "detach", "prompt": "x", "workflow": "general-task",
                            "label": "scrape"}) == []
    assert validate_action({"say": "s", "kind": "detach"})            # missing prompt → problems


def test_detach_from_conversation_writes_intent(tmp_path):
    ctx = _ctx(tmp_path, home="conversations_home")
    obs = detach.handle_detach(ctx, {"kind": "detach", "prompt": "scrape it",
                                     "workflow": "general-task", "label": "scrape"})
    assert not obs.get("rejected")
    assert obs["taskid"].startswith("bg-c-1-") and obs["label"] == "scrape"
    reqs = list((ctx.server.background_home / ".requests").glob("*.json"))
    assert len(reqs) == 1
    body = json.loads(reqs[0].read_text())
    assert body["owner"] == {"slug": "c-1", "dir": str(ctx.routine.dir)}
    assert body["prompt"] == "scrape it" and body["workflow"] == "general-task"


def test_detach_rejected_outside_root_conversation(tmp_path):
    for ctx in (_ctx(tmp_path, home="routines_home"),            # a scheduled routine
                _ctx(tmp_path, home="background_home"),           # a detached task itself
                _ctx(tmp_path, home="conversations_home", depth=1)):   # a within-reply child
        obs = detach.handle_detach(ctx, {"kind": "detach", "prompt": "x"})
        assert obs["rejected"] and "conversation" in obs["reason"]
    assert not list((tmp_path / "background" / ".requests").glob("*.json"))   # nothing written


def test_is_detached_run(tmp_path):
    assert detach.is_detached_run(_ctx(tmp_path, home="background_home")) is True
    assert detach.is_detached_run(_ctx(tmp_path, home="conversations_home")) is False


def test_detached_run_defers_blocking_ask(tmp_path, scripted):
    """A detached task coerces every ask to deferred — it can never park in waiting_user (which
    would hold a self-update restart in 'defer' with no user to answer)."""
    bg = tmp_path / "background"
    d = bg / "bg-task"
    (d / "state").mkdir(parents=True)
    (d / "inbox").mkdir()
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": "bg-task", "name": "t", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "general-task", "library_commit": ""},
        "budgets": {"max_turns": 10, "ask_timeout_min": 1}}), encoding="utf-8")
    (d / "instruction.md").write_text("do it", encoding="utf-8")
    (d / "main.md").write_text(WORKFLOW_MD, encoding="utf-8")
    (d / "LEDGER.md").write_text("# L\n", encoding="utf-8")
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.background_home = bg
    scripted([{"say": "asking", "kind": "ask_user", "question": "which way?",
               "mode": "blocking", "default": "left"},
              finish(summary="did it")])
    status, run_dir = run_routine(d, server, run_ts="20260715-120000")
    assert status == "ok"                                     # it did not block on the ask
    pend = list((d / "questions" / "pending").glob("*.json"))
    assert len(pend) == 1 and json.loads(pend[0].read_text())["mode"] == "deferred"
