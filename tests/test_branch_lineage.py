"""A branch of a branch: the WHOLE fork chain, and why child numbering needed no fix (E3.7).

One subject, in its own file because `tests/test_branches.py` and `tests/test_refusal_flag.py`
are both at their size-ratchet ceilings and a file over the bar may only shrink.

THE DEFECT (b). A branch recorded only its immediate fork point (`branched_from`), and
`rewind._leg_start` bounds a reply at the LATEST `finish` or fork cut before it. A fork is
indexed by TURN, so it can land MID-REPLY — the inherited reply it cut then has no finish of its
own. One level deep that is fine: the single recorded fork is the latest bound there is. Two
levels deep, an INHERITED reply lying before the immediate fork found no bound at all, because
the fork cut is after it and the grandparent's fork (nowhere in the header) had cut its
predecessor in half. Such a reply read as the conversation's FIRST — so the operator's ⚑ refusal
flag would have discarded the entire inherited history and re-sent `instruction.md` in place of
the message that really opened it.

THE SIBLING WORRY (a) NEEDED NO FIX, and is pinned here instead of built: a branch copies no
`runs/`, and a child number is claimed from the DISK rather than from the transcript.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import seeded_server
from rsched import branches
from rsched.engine.rewind import reply_opening

TS = "20261001-090000"
REPLY1_TS = "2026-10-01T09:01:00+00:00"
REPLY2_TS = "2026-10-01T09:05:00+00:00"
HEADER = {"type": "header", "run_id": f"c-flag:{TS}", "routine": "c-flag", "depth": 0}
#: The framing note `boot` re-authors on every leg (`replay: False`), which sits between a
#: delivered message and the reply it opened — `reply_opening` walks back over it.
RESUME_NOTE = {"type": "user_injection",
               "payload": {"text": "this run already ENDED (status ok) — …", "source": "engine",
                           "replay": False}}


def turn(n: int, kind: str = "read_file", **payload) -> dict:
    return {"ts": f"t{n}", "type": "assistant_action", "turn": n,
            "usage": {"in": 9, "out": 1, "model": "dummy/m"},
            "payload": {"kind": kind, "say": "s", **payload}}


def obs(n: int) -> dict:
    return {"ts": f"t{n}", "type": "observation", "turn": n,
            "payload": {"kind": "read_file", "content": "PLAN"}}


def reply(turns: int, ts: str, summary: str) -> dict:
    return {"ts": ts, "type": "finish", "turns": turns,
            "payload": {"status": "ok", "summary": summary, "authored": True}}


def said(text: str, **extra) -> dict:
    return {"ts": "t", "type": "user_injection", "payload": {"text": text, **extra}}


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


#: The parent conversation these forks come from: header, two complete turns, a finish.
PARENT_EVENTS = [
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


def _parent(server):
    """A finished conversation on disk, ready to fork."""
    d = server.conversations_home / "c-p"
    (d / "runs" / "20260827-100000").mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "c-p", "name": "Parent", "kind": "conversation", "models": {"main": "m"}}),
        encoding="utf-8")
    (d / "runs" / "20260827-100000" / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in PARENT_EVENTS), encoding="utf-8")
    return d


def _two_level_events():
    """A branch of a branch, as a transcript: the grandparent forked at turn 1 (cutting its
    reply in half, which is what a turn-indexed fork does), the middle branch ran a COMPLETE
    reply of its own, and this branch forked out of it at turn 4. Everything through index 12
    is inherited history.
    """
    return [{**HEADER, "branched_from": {"slug": "c-mid", "turn": 4},
             "lineage": [{"slug": "c-grand", "turn": 1}, {"slug": "c-mid", "turn": 4}]},
            said("the grandparent's ask"), turn(1), obs(1),       # 1-3: grandparent, cut at 3
            RESUME_NOTE, said("the middle branch's ask"),         # 4-5
            turn(2), obs(2), reply(2, REPLY1_TS, "mid replied"),  # 6-8: a COMPLETE reply
            RESUME_NOTE, said("the middle's second ask"),         # 9-10
            turn(4), obs(4),                                      # 11-12: this fork's cut
            RESUME_NOTE, said("this branch's own ask"),           # 13-14
            turn(5, "finish", status="ok", summary="x"),          # 15
            reply(5, REPLY2_TS, "x")]                             # 16


def test_an_inherited_reply_in_a_branch_of_a_branch_is_not_the_conversations_first():
    """The defect, as behaviour: with the whole chain in the header the bound is the latest
    fork BEFORE the finish — the grandparent's — and the reply reads as what it is."""
    opening = reply_opening(_two_level_events(), 2, REPLY1_TS)

    assert opening is not None
    assert opening["first"] is False, "an inherited reply is not the conversation's first"
    assert [e["payload"]["text"] for e in opening["messages"]] == ["the middle branch's ask"]
    assert opening["keep"] == 4          # past the grandparent's fork, less the resume note


def test_the_branchs_own_reply_still_starts_after_all_of_it():
    """The case that already worked keeps working: the latest fork bounds the latest reply."""
    opening = reply_opening(_two_level_events(), 5, REPLY2_TS)
    assert opening is not None and not opening["first"]
    assert [e["payload"]["text"] for e in opening["messages"]] == ["this branch's own ask"]


def test_a_branch_with_no_lineage_still_reads_its_own_fork():
    """Every branch forked before this change has `branched_from` and no `lineage`, and those
    conversations keep working: the single fork point is the bound, exactly as before.
    """
    events = [{**HEADER, "branched_from": {"slug": "c-parent", "turn": 1}},
              said("parent's ask"), turn(1), obs(1),
              RESUME_NOTE, said("the branch's own ask"),
              turn(2, "finish", status="ok", summary="x"), reply(2, REPLY2_TS, "x")]
    opening = reply_opening(events, 2, REPLY2_TS)
    assert [e["payload"]["text"] for e in opening["messages"]] == ["the branch's own ask"]
    assert opening["keep"] == 4


def test_a_branch_of_a_branch_records_the_whole_lineage(server):
    """The WRITER half: both places, because different code reads each — the config (which
    `api_branches` walks to render the lineage) and the transcript HEADER (what `rewind`
    reads).
    """
    d = _parent(server)
    first = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    mid_dir = server.conversations_home / first["slug"]

    second = branches.fork_conversation(server, parent_dir=mid_dir,
                                        parent_slug=first["slug"], at_turn=1)
    raw = yaml.safe_load((server.conversations_home / second["slug"] / "routine.yaml").read_text())

    # a FIRST-level branch's lineage is exactly its one fork — no empty entry, no self-reference
    mid_raw = yaml.safe_load((mid_dir / "routine.yaml").read_text())
    assert mid_raw["lineage"] == [{"slug": "c-p", "turn": 1}]
    # …and the second level carries both, oldest first
    assert raw["lineage"] == [{"slug": "c-p", "turn": 1},
                              {"slug": first["slug"], "turn": 1}], raw["lineage"]
    assert raw["parent"]["slug"] == first["slug"]    # the immediate parent is still recorded

    bdir = server.conversations_home / second["slug"]
    evs = [json.loads(x) for x in
           (next((bdir / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    assert evs[0]["lineage"] == raw["lineage"]
    assert evs[0]["branched_from"] == {"slug": first["slug"], "turn": 1}


def test_a_branchs_first_child_cannot_reuse_an_inherited_number(server):
    """E3.7(a), verified rather than built — the order said "fix it, or delete this half if it
    does not", and it does not.

    The worry was real in shape: a branch copies its parent's transcript, `subrun_start` events
    included, and the sub-run tree and every hand-back path key on the child NUMBER. Two facts
    make the collision impossible, and both are load-bearing enough to pin:

    1. A branch copies `state` and `attachments` only (`branches.COPIED_TREES`) — never `runs/`
       — so no child DIRECTORY is inherited. The copied `subrun_start` events are history of a
       run in another conversation.
    2. A number is claimed from the DISK: `childrun.claim_child` takes
       `_highest_child_number(root_run_dir) + 1` and still guards the mkdir against
       FileExistsError. What the inherited transcript says never enters the allocation.

    Either changing would reintroduce the collision silently — a future `COPIED_TREES` carrying
    `runs` would hand a branch its parent's child dirs, and an allocator reading the transcript
    would collide on the very first child.
    """
    from rsched.engine import childrun

    assert "runs" not in branches.COPIED_TREES, branches.COPIED_TREES

    d = _parent(server)
    made = branches.fork_conversation(server, parent_dir=d, parent_slug="c-p", at_turn=1)
    bdir = server.conversations_home / made["slug"]
    assert not (bdir / "runs" / "sub").exists()
    run_dir = next((bdir / "runs").iterdir())
    # a fresh branch run has no child directories, so the first child is 1 — and would be 1
    # even if the inherited transcript claimed children, which is the point
    assert childrun._highest_child_number(run_dir) == 0

    (run_dir / "sub" / "1").mkdir(parents=True)
    assert childrun._highest_child_number(run_dir) == 1, "the DISK is the record"
