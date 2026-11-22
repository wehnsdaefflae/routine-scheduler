"""The FACTS a hand-back carries, server-side (D306 pt 3), and the conversation row that makes
the lineage read optional (pt 5). The renderer is covered in the browser (tests/ui/
test_handback_card.py); these are the seams it reads: the paths ride the inbox record's `extra`,
survive the drain and reach the injection event, and a list row names its own parent.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import seeded_server
from rsched import branches, registry
from rsched.engine import inbox
from rsched.paths import atomic_write_json
from rsched.web.conversations_common import _item

PARENT_EVENTS = [
    {"type": "header", "run_id": "c-p:20260827-100000", "routine": "c-p",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 900, "out": 40},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "finish", "turn": 1, "payload": {"status": "ok", "summary": "parent replied"}},
]


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


def _conversation(server, slug, name, **extra):
    d = server.conversations_home / slug
    (d / "runs" / "20260827-100000").mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": slug, "name": name, "kind": "conversation", "description": name,
         "models": {"main": "m"}, **extra}), encoding="utf-8")
    (d / "main.md").write_text("recipe", encoding="utf-8")
    (d / "runs" / "20260827-100000" / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in PARENT_EVENTS), encoding="utf-8")
    atomic_write_json(d / "runs" / "20260827-100000" / "status.json",
                      {"state": "finished", "turn": 1})
    return d


def test_a_hand_back_carries_its_landed_paths_on_the_record(server):
    """The renderer must not parse prose: the paths `collect_handback` returned ride the inbox
    record itself, beside the branch's slug and its display name."""
    parent = _conversation(server, "c-p", "Parent")
    made = branches.fork_conversation(server, parent_dir=parent, parent_slug="c-p", at_turn=1)
    branch_dir = server.conversations_home / made["slug"]
    (branch_dir / "artifacts").mkdir(exist_ok=True)
    (branch_dir / "artifacts" / "verdict.md").write_text("option B wins", encoding="utf-8")

    branches.hand_back(server, branch_dir=branch_dir, slug=made["slug"], summary="B wins")

    msg = next(iter((parent / "inbox").glob("msg-branch-*.json")))
    rec = json.loads(msg.read_text())
    assert rec["via"] == "branch"
    assert rec["handback"]["slug"] == made["slug"]
    assert rec["handback"]["paths"] == [f"artifacts/from-branch-{made['slug']}/verdict.md"]
    # the prose still names them too — the card is a better reading of the same delivery
    assert "verdict.md" in rec["text"]


def test_the_drain_keeps_the_hand_back_record(server, tmp_path):
    """A key the drain does not carry is a key the engine never records: the narrowed item
    dict is the whole of what a consumer sees."""
    d = _conversation(server, "c-q", "Q")
    inbox.file_message(d, "handed back", via="branch", name="branch-c-b1-x",
                       extra={"handback": {"slug": "c-b1", "name": "B", "paths": ["a/b.md"]}})
    inbox.file_message(d, "plain freight", via="web")

    out = inbox.drain_messages(d, tmp_path / "consumed")

    # by TEXT, not by position: the drain's order is its filenames', and a hand-back's stem is
    # an idempotency key (`msg-branch-<slug>-<ts>`), not a timestamp that sorts with the rest.
    got = {m["text"]: m.get("handback") for m in out}
    assert got == {"handed back": {"slug": "c-b1", "name": "B", "paths": ["a/b.md"]},
                   "plain freight": None}


def test_a_conversation_row_names_its_own_parent(server):
    """pt 5: the row carries the raw `parent` slug, so a view derives "has a parent" from its
    own row and "has branches" from any row naming it — without the lineage route, which reads
    every conversation's routine.yaml.
    """
    parent = _conversation(server, "c-p", "Parent")
    made = branches.fork_conversation(server, parent_dir=parent, parent_slug="c-p", at_turn=1)

    catalog = registry.scan(server, server.conversations_home)
    rows = {slug: _item(info) for slug, info in catalog.items()}

    assert rows["c-p"]["parent"] == ""
    assert rows[made["slug"]]["parent"] == "c-p"
    # …which is what "has branches" is derived from, with no further read
    assert [s for s, r in rows.items() if r["parent"] == "c-p"] == [made["slug"]]


def test_the_parent_record_survives_the_config_load(server):
    """`extra="ignore"` drops an undeclared key, so `parent` is DECLARED on RoutineConfig —
    exactly as `owner` is for a detached task. Without it the catalog cannot see a branch's
    parent at all and every conversation view has to ask the lineage route.
    """
    parent = _conversation(server, "c-p", "Parent")
    made = branches.fork_conversation(server, parent_dir=parent, parent_slug="c-p", at_turn=1)

    cfg, problems = registry.load_config(server.conversations_home / made["slug"])

    assert cfg is not None
    assert cfg.parent["slug"] == "c-p"
    assert cfg.lineage == [{"slug": "c-p", "turn": 1}]
    # and neither key reads as an unknown routine.yaml key
    assert [p for p in problems if "unknown routine.yaml key" in p] == []
