"""How long an abort went UNSEEN, recorded (F597).

The defect: an abort that goes unseen for 30-60 seconds is unreproducible, because nothing
recorded how long it took to land. The door's moment and the engine's moment were both known
and never subtracted: `abortmark.write` already stamps the breadcrumb with `ts`, the engine
resolves it at close-out, and in between sits exactly the interval three runs of real
measurement failed to reproduce without instrumentation.

What is pinned here: the interval is a FIELD (`unseen_s`), computed at the one place that holds
both ends; it rides the finish event and the health row beside F751's `actor`/`cause`, so "which
aborts took long to land" is a filter and not a reading of result.md; and the sentences F751
pinned are untouched, because an actor's name is not a measurement. An abort whose door left no
stamp (a signal handler, a parent's kill) has no interval rather than a zero — nothing measured
is not "instant", which is the same distinction `actor: unknown` keeps.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from rsched import abortmark
from rsched.paths import atomic_write_json, read_json


@pytest.fixture(autouse=True)
def _no_stale_signal():
    abortmark.forget()
    yield
    abortmark.forget()


def _mark(run_dir, *, ts: str, actor: str = "user", pid: int | None = None) -> None:
    atomic_write_json(run_dir / abortmark.MARK_FILE,
                      {"actor": actor, "reason": "cancelled from the console", "ts": ts,
                       "pid": pid if pid is not None else os.getpid()})


def test_the_interval_between_the_door_and_the_engine_is_measured(tmp_path):
    """The whole point of the item: a door that signalled 42 seconds ago is a 42-second
    unseen interval, available as a number.
    """
    when = datetime.now(UTC) - timedelta(seconds=42)
    _mark(tmp_path, ts=when.isoformat(timespec="seconds"))
    mark = abortmark.resolve(tmp_path)
    assert mark.actor == "user"
    assert mark.unseen_s is not None
    assert 40 <= mark.unseen_s <= 46, mark.unseen_s


def test_an_abort_seen_at_once_measures_about_zero(tmp_path):
    """The common case must read as a small number, not as nothing: a zero-ish interval is
    evidence the engine was responsive, which is exactly what an investigation needs to rule
    out.
    """
    _mark(tmp_path, ts=datetime.now(UTC).isoformat(timespec="seconds"))
    mark = abortmark.resolve(tmp_path)
    assert mark.unseen_s is not None and 0 <= mark.unseen_s <= 2, mark.unseen_s


def test_a_door_that_left_no_stamp_has_no_interval_rather_than_a_zero(tmp_path):
    """A signal handler records no file (it is the witness for a stop no door wrote), and a
    parent's kill never leaves the process. Neither has a door moment, so neither has an
    interval — reporting 0 would claim a measurement nobody made.
    """
    abortmark.signalled("signal", "SIGTERM")
    assert abortmark.resolve(tmp_path).unseen_s is None

    ev = abortmark.AbortEvent()
    ev.set("the parent run killed this child")
    killed = abortmark.resolve(tmp_path, ev)
    assert killed.actor == "parent" and killed.unseen_s is None

    assert abortmark.resolve(tmp_path / "nothing-here").unseen_s is None


def test_a_mark_with_an_unreadable_ts_has_no_interval(tmp_path):
    """Robustness where it matters: the mark is written best-effort by a door that is about to
    signal, so a malformed `ts` must cost the measurement and nothing else — the actor still
    has to be named.
    """
    _mark(tmp_path, ts="not-a-timestamp")
    mark = abortmark.resolve(tmp_path)
    assert mark.actor == "user" and mark.unseen_s is None


def test_a_clock_that_went_backwards_is_not_a_negative_interval(tmp_path):
    """A door's stamp can be AHEAD of the engine's clock (a correction between the two
    reads). A negative wait is not a thing that happened, so it floors at zero rather than
    filing a number no reader can interpret.
    """
    ahead = datetime.now(UTC) + timedelta(seconds=30)
    _mark(tmp_path, ts=ahead.isoformat(timespec="seconds"))
    assert abortmark.resolve(tmp_path).unseen_s == 0.0


def test_a_foreign_pid_still_yields_no_interval(tmp_path):
    """A resume reuses the run directory, so a mark naming another pid is not about this leg.
    It is ignored for the ACTOR already (F751); the interval must not leak past that check
    either, or a stale mark would file a measurement about a previous leg.
    """
    _mark(tmp_path, ts=datetime.now(UTC).isoformat(timespec="seconds"), pid=os.getpid() + 99999)
    mark = abortmark.resolve(tmp_path)
    assert mark.actor == "unknown" and mark.unseen_s is None


def test_the_written_mark_round_trips_through_resolve(tmp_path):
    """`write` is the door's own call, so the stamp it leaves must be the one `resolve`
    measures from — pinned together, because the two halves living in one module is the only
    reason this interval is computable at all.
    """
    abortmark.write(tmp_path, actor="user", reason="stop", pid=os.getpid())
    rec = read_json(tmp_path / abortmark.MARK_FILE)
    assert isinstance(rec, dict) and rec["ts"]
    assert abortmark.resolve(tmp_path).unseen_s is not None


def test_the_interval_rides_the_finish_payload_and_the_health_row(tmp_path, monkeypatch):
    """The field has to reach the surfaces an investigation reads: the finish event (beside
    F751's `actor`/`cause`) and the health row. A number computed and not filed is the defect
    this item is about, one layer up.
    """
    from rsched.engine import loopend

    when = datetime.now(UTC) - timedelta(seconds=37)
    _mark(tmp_path, ts=when.isoformat(timespec="seconds"))
    mark = abortmark.resolve(tmp_path)

    events: list[dict] = []
    monkeypatch.setattr(loopend, "log_health_event",
                        lambda home, event, **f: events.append({"event": event, **f}))

    payload = loopend.abort_fields(mark)
    assert payload["actor"] == "user" and payload["cause"] == "user_abort"
    assert 35 <= payload["unseen_s"] <= 41, payload

    loopend.log_health_event(tmp_path, "run_failed", routine="r", run_id="x",
                             **loopend.abort_fields(mark))
    assert events and 35 <= float(events[0]["unseen_s"]) <= 41, events


def test_the_sentence_is_unchanged_by_the_measurement(tmp_path):
    """F751's finish text is pinned elsewhere and must stay exactly what it was: who stopped
    the run is a name, and how long it took to land is a number. Mixing them would make one
    test's subject two.
    """
    _mark(tmp_path, ts=(datetime.now(UTC) - timedelta(seconds=99)).isoformat(timespec="seconds"))
    assert abortmark.resolve(tmp_path).sentence == (
        "Run aborted by the user — cancelled from the console.")


def test_a_threading_event_without_a_reason_is_still_a_parent_abort(tmp_path):
    """The existing fallback (a set event with no reason) keeps working and gains no
    interval — guarding the ordering inside `resolve`, which the new field must not disturb.
    """
    ev = threading.Event()
    ev.set()
    mark = abortmark.resolve(tmp_path, ev)
    assert mark.actor == "parent" and mark.unseen_s is None
