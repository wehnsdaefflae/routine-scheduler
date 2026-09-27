"""The oversight ladder's pure half: what travels UP (the dispatch) and what comes DOWN.

These tests are deliberately about CONTENT, not about flags. The design's whole claim is that
"the discrepancy between a worker's 'verifying the output' and a histogram showing 14 read_file
/ 0 write_file is a finding no single self-authored account can produce" — so a test that only
asserted `dispatch["histogram"] is not None` would pass on a dispatch that lost the very
discrepancy the feature exists to surface. Each test below names the wrong behaviour it fails on.
"""

from __future__ import annotations

import json

import pytest

from rsched.engine import oversight


def _write_transcript(path, events):
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _action(turn, kind, say, **extra):
    return {"ts": f"2026-09-27T00:{turn:02d}:00+02:00", "type": "assistant_action",
            "turn": turn, "payload": {"kind": kind, "say": say, **extra}}


# --------------------------------------------------------------------------- dispatch

def test_dispatch_carries_raw_say_lines_not_a_summary(tmp_path):
    """Rung 1 reads the worker's OWN words. Fails if the dispatch ever starts summarizing
    them — 'the party under suspicion must not be the only witness'."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [
        {"type": "header", "run_id": "r:1", "routine": "r"},
        _action(1, "read_file", "Looking at the config."),
        _action(2, "read_file", "Reading the other half."),
        _action(3, "write_file", "Writing the result."),
    ])

    d = oversight.build_dispatch(t, goal="Ship the thing.", since_turn=0, turn=3,
                                budget={"turns_left": 97, "max_turns": 100})

    assert d["says"] == ["Looking at the config.", "Reading the other half.",
                         "Writing the result."]
    assert d["goal"] == "Ship the thing."


def test_dispatch_histogram_counts_action_kinds(tmp_path):
    """The histogram is the evidence a self-report cannot fake. Fails if kinds are collapsed
    or the counts drift from the say lines they came from."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [
        _action(1, "read_file", "a"), _action(2, "read_file", "b"),
        _action(3, "read_file", "c"), _action(4, "util", "d"),
    ])

    d = oversight.build_dispatch(t, goal="g", since_turn=0, turn=4, budget={})

    assert d["histogram"] == {"read_file": 3, "util": 1}


def test_dispatch_covers_only_turns_since_the_last_rung(tmp_path):
    """An escalation reports on the interval it owns. Fails if a rung re-reads turns a
    previous rung already judged, which is how a ladder becomes m copies of one audit."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [_action(n, "read_file", f"say {n}") for n in range(1, 8)])

    d = oversight.build_dispatch(t, goal="g", since_turn=4, turn=7, budget={})

    assert d["says"] == ["say 5", "say 6", "say 7"]
    assert d["since_turn"] == 4


def test_dispatch_flags_an_action_string_repeated_more_than_twice(tmp_path):
    """A repetition signal the engine can see without asking the worker. Fails if a run
    looping on one call reaches its supervisor looking like ordinary progress."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [
        _action(1, "util", "Trying the fetch.", name="web-request", args=["http://x"]),
        _action(2, "util", "Trying the fetch.", name="web-request", args=["http://x"]),
        _action(3, "util", "Trying the fetch.", name="web-request", args=["http://x"]),
        _action(4, "read_file", "Something else."),
    ])

    d = oversight.build_dispatch(t, goal="g", since_turn=0, turn=4, budget={})

    assert d["repetition"], "a 3x identical action must raise a repetition signal"
    assert any("3" in s and "web-request" in s for s in d["repetition"])


def test_dispatch_flags_a_failed_call_retried_unchanged(tmp_path):
    """The second half of the repetition contract: an action whose observation FAILED and was
    then re-emitted byte-identically. Fails if only literal repetition is caught, which would
    miss the retry-the-same-broken-call loop the design names."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [
        _action(1, "util", "Fetch.", name="web-request", args=["http://x"]),
        {"type": "observation", "turn": 1,
         "payload": {"ok": False, "text": "exit=2: usage: gu web-request ..."}},
        _action(2, "util", "Fetch.", name="web-request", args=["http://x"]),
        {"type": "observation", "turn": 2,
         "payload": {"ok": False, "text": "exit=2: usage: gu web-request ..."}},
    ])

    d = oversight.build_dispatch(t, goal="g", since_turn=0, turn=2, budget={})

    assert any("unchanged" in s for s in d["repetition"]), d["repetition"]


def test_dispatch_needs_no_model_and_no_network(tmp_path):
    """Pure extraction, so it is unit-testable against a recorded transcript — the property
    that makes step 1 shippable before any of the machinery exists."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [_action(1, "read_file", "x")])

    d = oversight.build_dispatch(t, goal="g", since_turn=0, turn=1, budget={})

    assert set(oversight.DISPATCH_KEYS) <= set(d)
    # the worker's three questions are ASKED here and answered by the worker itself
    assert d["worker_questions"] and len(d["worker_questions"]) == 3


def test_dispatch_on_an_empty_interval_is_still_a_dispatch(tmp_path):
    """A worker that emitted nothing since the last rung is itself the signal. Fails if the
    builder raises or returns None, which would make a stalled run un-supervisable."""
    t = tmp_path / "transcript.jsonl"
    _write_transcript(t, [_action(1, "read_file", "x")])

    d = oversight.build_dispatch(t, goal="g", since_turn=9, turn=9, budget={})

    assert d["says"] == []
    assert d["histogram"] == {}


# --------------------------------------------------------------------------- directive

def test_valid_directive_round_trips():
    ok = {"verdict": "drifting", "disposition": "redirect",
          "instruction": ["Stop re-reading the config.", "Write the file."],
          "next_look": "Whether a write_file actually happened.", "next_rung_in": 10}
    assert oversight.validate_directive(ok, cap=20) == ok


@pytest.mark.parametrize(("bad", "why"), [
    ({"verdict": "vibes", "disposition": "continue"}, "unknown verdict"),
    ({"verdict": "on_track", "disposition": "wander"}, "unknown disposition"),
    ({"verdict": "on_track"}, "missing disposition"),
    ({"disposition": "continue"}, "missing verdict"),
    ({"verdict": "drifting", "disposition": "redirect", "instruction": []},
     "a non-continue disposition with nothing to say"),
    ({"verdict": "drifting", "disposition": "redirect", "instruction": ["a"] * 6},
     "more than five lines of instruction"),
    ({"verdict": "on_track", "disposition": "continue", "next_rung_in": 0},
     "next_rung_in below one"),
])
def test_malformed_directive_is_refused(bad, why):
    """Every rejection names the field. A supervisor whose output is silently coerced is a
    supervisor whose authority nobody can audit."""
    with pytest.raises(ValueError):
        oversight.validate_directive(bad, cap=20)


def test_continue_may_not_carry_instruction():
    """Constraint 1 of the design, enforced at the schema rather than at the injection site:
    'a continue verdict must inject NOTHING'. Fails if a continue can smuggle prose down."""
    with pytest.raises(ValueError):
        oversight.validate_directive(
            {"verdict": "on_track", "disposition": "continue",
             "instruction": ["Keep going but also do this."]}, cap=20)


def test_next_rung_in_is_capped_at_n():
    """A supervisor may shorten its own next interval; it may never lengthen it past the
    operator's n. Fails if a supervisor can switch itself off by answering 9999."""
    d = oversight.validate_directive(
        {"verdict": "on_track", "disposition": "continue", "next_rung_in": 9999}, cap=15)
    assert d["next_rung_in"] == 15


def test_directive_renders_for_the_worker_only_when_it_says_something():
    """The rendering is what the worker reads, so it is tested as prose, not as a dict."""
    text = oversight.render_directive(
        {"verdict": "stalled", "disposition": "narrow",
         "instruction": ["Drop the browser probe.", "Land the parser test."],
         "next_look": "Whether the parser test exists.", "next_rung_in": 8}, rung=1)
    assert "stalled" in text and "narrow" in text
    assert "Drop the browser probe." in text and "Land the parser test." in text
    assert "rung 1" in text.lower()

    assert oversight.render_directive(
        {"verdict": "on_track", "disposition": "continue"}, rung=1) == ""
