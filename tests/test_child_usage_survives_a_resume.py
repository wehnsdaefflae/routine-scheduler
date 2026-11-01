"""A CHILD RUN's spend survives its parent's resume, and an orphan's is recovered (R3112).

`token-lab` measured (Exp #24, zero model calls, verified to the token on two of this builder's
runs) that `status.json`'s `usage.out` understates a run that spawned children — one run by 40 %.
The report's own reading was that the fold happens at REAP, so a child nobody waited for is
attributed nowhere. Re-measuring found two different, narrower causes, both pinned here:

1. **A resume rebuilds `usage_base` from the transcript and skipped `subrun_end`.** A child IS
   folded into the parent's live counter at collection (`subruns._collect` → `ctx.add_usage`), so
   within one leg the accounting is right — but `history.prior_usage` summed only assistant
   actions, llm/decide observations and compactions, so every child collected in an EARLIER leg
   vanished at the resume boundary. Measured on `scheduler-builder:20261029-140004`: two children
   exited at 16:03 and 16:08 carrying 420,211 output tokens between them, both before the leg's
   finish at 16:15; the resumed leg reported 343,918 out for a tree that cost about a million.

2. **An ORPHANED child's spend was written as `{}`.** A child that did not survive the
   interruption is never collected, and the resumed leg's marker recorded `usage: {}` while the
   real figure sat in `sub/<n>/transcript.jsonl` — 193,930 output tokens for sub/1 of
   `scheduler-builder:20261030-140004`.
"""

from __future__ import annotations

import json

from rsched.engine.history import prior_usage
from rsched.engine.subsalvage import spend_reached, turns_reached


def _write(path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def test_a_child_collected_in_an_earlier_leg_is_in_the_resumed_legs_base():
    """CAUSE 1. The specimen's shape: the parent's own turns, then two child exits, then the
    leg's finish. All of it is the resumed leg's base.
    """
    events = [
        {"type": "assistant_action", "usage": {"in": 100, "out": 50}},
        {"type": "subrun_end", "payload": {"n": 1, "status": "ok",
                                           "usage": {"in": 10, "out": 198793}}},
        {"type": "subrun_end", "payload": {"n": 2, "status": "ok",
                                           "usage": {"in": 20, "out": 221418}}},
        {"type": "finish", "payload": {"status": "ok"}},
    ]
    assert prior_usage(events) == {"in": 130, "out": 420261}


def test_the_other_sources_still_count_exactly_as_they_did():
    """The change ADDS a source; it must not disturb the three that were already summed, or a
    resumed run's base moves for every run rather than only for one with children.
    """
    events = [
        {"type": "assistant_action", "usage": {"in": 1, "out": 2}},
        {"type": "observation", "payload": {"kind": "llm", "usage": {"in": 4, "out": 8}}},
        {"type": "observation", "payload": {"kind": "decide", "usage": {"in": 16, "out": 32}}},
        {"type": "compaction", "payload": {"usage": {"in": 64, "out": 128}}},
        {"type": "observation", "payload": {"kind": "util"}},          # no usage at all
        {"type": "subrun_start", "payload": {"n": 1}},                 # no usage at all
    ]
    assert prior_usage(events) == {"in": 85, "out": 170}


def test_a_subrun_end_with_no_usage_adds_nothing_rather_than_raising():
    """An orphan marker written by a build BEFORE this change carries `usage: {}`, and a
    transcript is read by every later build: an absent or malformed reading must be inert.
    """
    events = [
        {"type": "assistant_action", "usage": {"in": 5, "out": 7}},
        {"type": "subrun_end", "payload": {"n": 1, "status": "aborted", "usage": {}}},
        {"type": "subrun_end", "payload": {"n": 2, "status": "aborted"}},
        {"type": "subrun_end", "payload": {"n": 3, "usage": "not a dict"}},
    ]
    assert prior_usage(events) == {"in": 5, "out": 7}


def test_an_orphans_own_transcript_gives_up_its_spend(tmp_path):
    """CAUSE 2. The dead child's figures come from its OWN transcript, summed by the same
    definition the parent uses — so a recovered total cannot mean something different.
    """
    child = tmp_path / "sub" / "1"
    _write(child / "transcript.jsonl", [
        {"type": "header", "payload": {"label": "builder"}},
        {"type": "assistant_action", "turn": 1, "usage": {"in": 7, "out": 193000}},
        {"type": "observation", "turn": 1, "payload": {"kind": "llm",
                                                       "usage": {"in": 3, "out": 930}}},
        {"type": "observation", "turn": 2, "payload": {"kind": "util"}},
    ])
    assert spend_reached(child) == {"in": 10, "out": 193930}
    assert turns_reached(child) == 2


def test_a_child_that_recorded_nothing_yields_nothing(tmp_path):
    """`{}` still means "nothing recorded there" — the discipline `turns_reached` already keeps:
    what the disk says, never an estimate.
    """
    child = tmp_path / "sub" / "2"
    _write(child / "transcript.jsonl", [{"type": "header", "payload": {}}])
    assert spend_reached(child) == {}
    assert turns_reached(child) == 0


def test_a_child_dir_that_is_gone_is_not_an_error(tmp_path):
    """Retention prunes runs, and a resumed leg must boot whatever is left of its tree."""
    assert spend_reached(tmp_path / "sub" / "99") == {}
    assert turns_reached(tmp_path / "sub" / "99") == 0


def test_an_unparseable_line_in_a_childs_transcript_is_skipped(tmp_path):
    child = tmp_path / "sub" / "3"
    child.mkdir(parents=True)
    (child / "transcript.jsonl").write_text(
        '{"type": "assistant_action", "turn": 1, "usage": {"in": 2, "out": 4}}\n'
        "{ this is not json\n"
        '{"type": "assistant_action", "turn": 2, "usage": {"in": 8, "out": 16}}\n',
        encoding="utf-8")
    assert spend_reached(child) == {"in": 10, "out": 20}


def test_a_grandchilds_spend_is_counted_once_through_its_own_parent(tmp_path):
    """A child's transcript carries its OWN children's `subrun_end` rows, folded into it at
    their collection — so summing them here is what makes a recovered total cover the whole
    subtree, and the parent counts the child once rather than the tree twice.
    """
    child = tmp_path / "sub" / "4"
    _write(child / "transcript.jsonl", [
        {"type": "assistant_action", "turn": 1, "usage": {"in": 1, "out": 2}},
        {"type": "subrun_end", "payload": {"n": 1, "status": "ok",
                                           "usage": {"in": 100, "out": 200}}},
    ])
    assert spend_reached(child) == {"in": 101, "out": 202}
