"""A resumed run that finishes `ok` must read `ok` on every surface (F637 + F703).

A run killed by three rejections of the same correctable mistake, then resumed and finished
`ok`, left two surfaces permanently disagreeing about whether it failed. The run's OWN record
self-corrects — the resumed leg overwrites status.json and result.md, and `usage_stream`'s
leg fold keeps the newest leg's status — but the health stream is APPEND-ONLY and has no
vocabulary for "that ending was superseded": its `run_failed` / `budget_exhausted` /
`attempts_exhausted` rows say the run failed, forever, to every reader of the fold.

So a correction is a LATER EVENT that the readers resolve to, never an edit of the original.
The original row really happened and stays in the history — the audit trail is the whole point
of an append-only stream. These tests pin both directions, because a correction that fires too
eagerly is worse than the bug: a run that stayed failed must still read failed.
"""

import json
from datetime import UTC, datetime, timedelta

from conftest import finish, write_file
from helpers import health_events, server_for, tmp_server
from rsched.engine.runtime import run_routine
from rsched.readmodels import memo
from rsched.readmodels.health_stream import blocked_fleet, budget_endings

TS = "20260708-070000"


def _ago(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _write(server, rows):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


# --------------------------------------------------------------------------- the writer


def test_a_resumed_run_that_finishes_ok_supersedes_its_own_failure(
        make_routine, scripted, tmp_path):
    """F637's own case: three rejections of the same correctable mistake end the run `failed`,
    the operator resumes it, and the resumed leg finishes `ok`. The failure stays on the record
    (it happened); a `run_corrected` row beside it says the run did not, in the end, fail.
    """
    d = make_routine(slug="corrected")
    scripted(["nope one", "nope two", "nope three"])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "failed"
    home = tmp_path / "routines"
    assert len(health_events(home, event="run_failed")) == 1

    scripted([write_file("state/done.txt", say="Picking it up."),
              finish("ok", "the rejected action was corrected; everything asked for is done")])
    status2, _ = run_routine(d, server_for(d), run_ts=TS, resume_from=TS)
    assert status2 == "ok"

    # the original is untouched — a correction SUPERSEDES, it does not erase
    assert len(health_events(home, event="run_failed")) == 1
    rows = health_events(home, event="run_corrected")
    assert len(rows) == 1, "a resumed-to-ok run files no correction"
    row = rows[0]
    assert row["routine"] == "corrected"
    assert row["run_id"] == f"corrected:{TS}"
    assert "run_failed" in row["superseded"]
    assert "attempts_exhausted" in json.dumps(row)      # the wrong-turn row that ended the run


def test_a_budget_exhausted_run_resumed_to_ok_is_corrected(make_routine, scripted, tmp_path):
    """F703's own case: nothing could supersede a `budget_exhausted` row."""
    d = make_routine(slug="budcorrect", budgets={"max_turns": 3})
    scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(3)],
              finish("partial", "out of turns, resume here")])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    home = tmp_path / "routines"
    assert len(health_events(home, event="budget_exhausted")) == 1

    scripted([finish("ok", "finished what the first leg could not")])
    assert run_routine(d, server_for(d), run_ts=TS, resume_from=TS)[0] == "ok"
    rows = health_events(home, event="run_corrected")
    assert len(rows) == 1 and "budget_exhausted" in rows[0]["superseded"]


def test_a_run_that_stayed_failed_is_not_corrected(make_routine, scripted, tmp_path):
    """The other direction. A correction that fires too eagerly is worse than the bug."""
    d = make_routine(slug="stillfailed")
    scripted(["nope one", "nope two", "nope three"])
    assert run_routine(d, server_for(d), run_ts=TS)[0] == "failed"
    home = tmp_path / "routines"
    assert health_events(home, event="run_corrected") == []

    # resumed, and still not ok: a partial corrects nothing
    scripted([finish("partial", "still blocked on the same source")])
    assert run_routine(d, server_for(d), run_ts=TS, resume_from=TS)[0] == "partial"
    assert health_events(home, event="run_corrected") == []


def test_an_ordinary_ok_run_files_no_correction(make_routine, scripted, tmp_path):
    """A first-and-only leg that finishes ok has nothing to supersede — no row, no noise."""
    d = make_routine(slug="plainok")
    scripted([write_file("state/a.txt", say="One step."), finish("ok", "done")])
    assert run_routine(d, server_for(d), run_ts=TS)[0] == "ok"
    assert health_events(tmp_path / "routines", event="run_corrected") == []


# --------------------------------------------------------------------------- the readers


def test_the_blocked_surface_reads_the_correction(tmp_path):
    """F703: the blocked-work surface reads the CORRECTION rather than the original. A turn
    that exhausted its attempts ended the run — but the run was resumed and finished ok, so
    the work it owed was not lost, and the fold must stop counting it."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "wrong_turn_detected", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(4), "reason": "attempts_exhausted", "turn": 7, "detail": "turn 7"},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(1),
         "superseded": ["run_failed", "wrong_turn_detected"], "detail": "resumed to ok"},
        # an uncorrected run stays exactly as blocked as it was
        {"event": "wrong_turn_detected", "routine": "beta", "run_id": "beta:r1",
         "ts": _ago(3), "reason": "attempts_exhausted", "turn": 2, "detail": "turn 2"},
    ])
    out = blocked_fleet(server)
    subjects = [(r["event"], r["subject"]) for r in out["rows"]]
    assert ("wrong_turn_detected", "beta") in subjects
    assert ("wrong_turn_detected", "alpha") not in subjects, \
        "a corrected run still reads as blocked work"
    assert out["total"] == 1


def test_a_turn_that_was_merely_rejected_is_not_folded_away(tmp_path):
    """A correction supersedes the rows that say THE RUN ENDED NOT-OK. It does not erase the
    measurement of turns that were paid for and produced nothing — `schema_rejected` and
    `repeat_invalid` rows are the corpus `engine/wrongturn.py` exists to accumulate, and a
    resume does not give that spend back."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "wrong_turn_detected", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(4), "reason": "schema_rejected", "turn": 3, "detail": "turn 3"},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(1),
         "superseded": ["run_failed"], "detail": "resumed to ok"},
    ])
    out = blocked_fleet(server)
    assert out["total"] == 1 and out["rows"][0]["event"] == "wrong_turn_detected"


def test_an_unchecked_finish_is_not_corrected_by_a_later_one(tmp_path):
    """`finish_gate_failed` does not claim the run failed — it claims the CHECKS that should
    have judged that ending never ran. A later, checked ending does not make the earlier
    unchecked one checked, so the row stands."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "finish_gate_failed", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(4), "detail": "AttributeError in a guard"},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(1),
         "superseded": ["run_failed"], "detail": "resumed to ok"},
    ])
    out = blocked_fleet(server)
    assert out["total"] == 1 and out["rows"][0]["event"] == "finish_gate_failed"
    assert out["rows"][0].get("corrected") is not True


def test_budget_endings_reads_the_correction(tmp_path):
    """The routine page's endings split must not keep calling a corrected run a partial
    finish. The count moves to `corrected` rather than vanishing: the budget really did end
    that leg."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(5), "status": "partial", "resource": "turns", "limit": 3},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(1),
         "superseded": ["budget_exhausted"], "detail": "resumed to ok"},
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "alpha:r2",
         "ts": _ago(4), "status": "partial", "resource": "turns", "limit": 3},
        {"event": "run_partial", "routine": "alpha", "run_id": "alpha:r3", "ts": _ago(3)},
    ])
    out = budget_endings(server, "alpha")
    assert out["budget_exhausted"] == 1, "a corrected run still counts as budget-exhausted"
    assert out["run_partial"] == 1
    assert out["corrected"] == 1


def test_a_run_that_fails_again_after_a_correction_is_blocked_again(tmp_path):
    """The resolution compares STAMPS, never a flat "this run was corrected once" flag: a run
    resumed to ok and then resumed again into a failure is failing now, and a reader that
    folded it away on the strength of the earlier correction would be hiding a live problem."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "wrong_turn_detected", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(6), "reason": "attempts_exhausted", "turn": 2, "detail": "first ending"},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(4),
         "superseded": ["wrong_turn_detected"], "detail": "resumed to ok"},
        {"event": "wrong_turn_detected", "routine": "alpha", "run_id": "alpha:r1",
         "ts": _ago(1), "reason": "attempts_exhausted", "turn": 9, "detail": "and again"},
    ])
    out = blocked_fleet(server)
    assert out["total"] == 1, "a later failure was excused by an earlier correction"
    assert out["rows"][0]["detail"] == "and again"


def test_a_second_correction_names_only_what_the_first_did_not(tmp_path):
    """`runcorrection._pending` stops at the run's own last correction, so a run resumed twice
    files a second row for the SECOND ending alone — not for everything it ever did."""
    from rsched import runcorrection

    records = [
        {"event": "run_failed", "routine": "a", "run_id": "a:r1", "ts": _ago(9)},
        {"event": "run_corrected", "routine": "a", "run_id": "a:r1", "ts": _ago(8),
         "superseded": ["run_failed"]},
        {"event": "budget_exhausted", "routine": "a", "run_id": "a:r1", "ts": _ago(3),
         "status": "partial", "resource": "turns"},
    ]
    assert runcorrection._pending(records, "a:r1") == (
        ["budget_exhausted"], ["budget_exhausted (turns)"])


def test_the_lane_chain_close_reads_the_correction(tmp_path):
    """F703 names the lane chain's not-ok close. The chain is NOT reopened — its remaining
    members really did not run, so the row stays on the blocked fold — but the row must stop
    claiming the member failed when that member's run was resumed and finished ok."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "lane_chain_stopped", "routine": "grp-1", "run_id": "lr-9", "ts": _ago(4),
         "detail": "morning: 2 member runs, 1 not-ok (alpha)", "stopped_by": "alpha:r1"},
        {"event": "run_corrected", "routine": "alpha", "run_id": "alpha:r1", "ts": _ago(1),
         "superseded": ["run_failed"], "detail": "resumed to ok"},
    ])
    out = blocked_fleet(server)
    assert out["total"] == 1                      # the members still did not run
    row = out["rows"][0]
    assert row["event"] == "lane_chain_stopped"
    assert row["corrected"] is True
    assert "alpha:r1" in row["detail"] and "ok" in row["detail"].lower()


def test_an_uncorrected_lane_close_is_unchanged(tmp_path):
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "lane_chain_stopped", "routine": "grp-1", "run_id": "lr-9", "ts": _ago(4),
         "detail": "morning: 2 member runs, 1 not-ok (alpha)", "stopped_by": "alpha:r1"},
    ])
    row = blocked_fleet(server)["rows"][0]
    assert row["detail"] == "morning: 2 member runs, 1 not-ok (alpha)"
    assert row.get("corrected") is not True


# --------------------------------------------------------------------------- registration


def test_the_event_is_in_the_documented_vocabulary():
    """The vocabulary is machine-checked (tests/test_health_events.py) and a reader keyed on
    the header enum sees nothing of an event missing from it."""
    from rsched import health_events as he
    from rsched import runcorrection

    assert f'"{runcorrection.EVENT}"' in (he.__doc__ or "")


def test_the_correction_is_never_cooled_down():
    """A correction is an incident, not a standing condition — and a suppressed correction
    leaves the original ending standing on every surface that resolves it."""
    from rsched import runcorrection
    from rsched.health_cooldown import REPEAT_COOLDOWN_HOURS

    assert runcorrection.EVENT not in REPEAT_COOLDOWN_HOURS


def test_the_correction_is_not_itself_blocked_work():
    """`BLOCKED_EVENTS` means work that was DUE and did not happen. A corrected run did its
    work — the row belongs nowhere near that fold."""
    from rsched import runcorrection
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert runcorrection.EVENT not in BLOCKED_EVENTS


def test_the_lane_close_names_the_member_run_that_stopped_it():
    """The lane row can only read a correction if it says WHICH run closed the chain: the
    detail named a slug, and a slug is not a run."""
    import inspect

    from rsched.daemon import lane_runs

    src = inspect.getsource(lane_runs.LaneRunManager._finalize)
    assert "stopped_by" in src
