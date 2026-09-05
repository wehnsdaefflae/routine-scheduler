"""Background archival: the run keeps its turn, the archive lands behind it, and the
lossless-archive default is not traded for the speed."""

import threading
import time
from types import SimpleNamespace

from rsched.engine import archival


def _loop(tmp_path, messages=None):
    events: list = []
    booked: list = []
    ctx = SimpleNamespace(
        run_dir=tmp_path,
        transcript=SimpleNamespace(event=lambda kind, payload, **k: events.append(
            (kind, payload))),
        add_usage=booked.append)
    loop = SimpleNamespace(ctx=ctx, turn_records=[], _hist_rel="runs/x/history",
                           _history_active=False, _hist_note_countdown=99,
                           messages=list(messages or [{"role": "user", "content": "digest"}]))
    archival.configure(loop)
    loop._events, loop._booked = events, booked
    return loop


def _fake_archive(result, *, delay=0.0):
    def archive_middle(middle, endpoint, ref, run_dir, turn):
        if delay:
            time.sleep(delay)
        if isinstance(result, Exception):
            raise result
        return result

    return archive_middle


def _run_to_completion(loop):
    pending = loop._archival
    if pending is not None:
        pending.thread.join(timeout=5)


# --- the happy path ---------------------------------------------------------------------

def test_the_run_keeps_its_turn_and_the_archive_lands_behind_it(tmp_path, monkeypatch):
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 3, "elided_messages": 8,
         "usage": {"in": 100, "out": 20}}))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}] * 8, object(), object(), 12)
    # nothing is appended while it runs — the run is mid-turn and owes the model nothing
    _run_to_completion(loop)
    archival.collect(loop)
    assert loop._history_active is True
    assert loop._booked == [{"in": 100, "out": 20}]      # the archival's own spend is booked
    note = loop.messages[-1]["content"]
    assert "finished archiving into a NAVIGABLE history" in note
    assert "runs/x/history/INDEX.md" in note and "3 files" in note
    kinds = [k for k, _ in loop._events]
    assert "compaction" in kinds and "user_injection" in kinds
    payload = next(p for k, p in loop._events if k == "compaction")
    assert payload["background"] is True and payload["mode"] == "llm-history"


def test_the_note_is_appended_and_the_digest_is_left_alone(tmp_path, monkeypatch):
    """Rewriting the digest would be a SECOND rewrite of the prefix and a second cache
    invalidation for one compaction. Appending costs nothing and keeps the message list
    append-only, which is the contract everywhere else."""
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 1, "elided_messages": 2}))
    head = [{"role": "system", "content": "S"}, {"role": "user", "content": "CONTEXT COMPACTED"}]
    loop = _loop(tmp_path, messages=head)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 4)
    _run_to_completion(loop)
    archival.collect(loop)
    assert loop.messages[:2] == head          # untouched, byte for byte
    assert len(loop.messages) == 3            # the note is the third message, appended


def test_collect_does_nothing_while_the_archive_is_still_running(tmp_path, monkeypatch):
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 1}, delay=5))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 1)
    archival.collect(loop)
    assert len(loop.messages) == 1 and loop._events == []
    assert loop._archival is not None         # still pending, still owed


def test_only_one_archival_runs_at_a_time(tmp_path, monkeypatch):
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 1}, delay=5))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 1)
    first = loop._archival
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 2)
    assert loop._archival is first            # the second is refused, not queued


# --- the degraded paths -------------------------------------------------------------------

def test_a_failed_archival_is_recorded_and_the_digest_stands(tmp_path, monkeypatch):
    """Exactly where the synchronous path landed on a failure — and visible, because a silent
    degrade is how a broken archival model goes unnoticed."""
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle",
                        _fake_archive(RuntimeError("archival model returned non-JSON")))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 3)
    _run_to_completion(loop)
    archival.collect(loop)
    assert len(loop.messages) == 1            # nothing announced; the digest is what stands
    assert loop._history_active is False
    payload = next(p for k, p in loop._events if k == "compaction")
    assert payload["archival_degraded"].startswith("archival model returned non-JSON")


def test_an_empty_archive_is_a_degrade_not_a_success(tmp_path, monkeypatch):
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(None))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 3)
    _run_to_completion(loop)
    archival.collect(loop)
    assert loop._history_active is False
    payload = next(p for k, p in loop._events if k == "compaction")
    assert "returned no files" in payload["archival_degraded"]


# --- settling at finish ---------------------------------------------------------------------

def test_a_finishing_run_settles_an_archive_that_is_nearly_done(tmp_path, monkeypatch):
    """Without this a run that ends shortly after compacting drops its archive on the floor —
    and the archive still has readers after the run: the search index covers history/."""
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 2, "usage": {"in": 5, "out": 1}}, delay=0.05))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 9)
    archival.settle(loop)
    assert loop._booked == [{"in": 5, "out": 1}]
    assert len(loop.messages) == 1            # no note: there is no next turn to read one
    payload = next(p for k, p in loop._events if k == "compaction")
    assert payload["history_files"] == 2 and payload["background"] is True


def test_a_finishing_run_abandons_an_archive_that_would_stall_it(tmp_path, monkeypatch):
    """Waiting the full archival timeout would put the 180-600s stall back, at the moment a
    conversation's reply is due. The digest and the transcript are what remain."""
    from rsched.engine import compaction

    monkeypatch.setattr(archival, "SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 1}, delay=5))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 9)
    started = time.monotonic()
    archival.settle(loop)
    assert time.monotonic() - started < 2     # it did not wait for the archival
    payload = next(p for k, p in loop._events if k == "compaction")
    assert payload["archival_abandoned"] is True
    assert "the digest and the full transcript stand" in payload["note"]


def test_settling_with_nothing_in_flight_is_a_no_op(tmp_path):
    loop = _loop(tmp_path)
    archival.settle(loop)
    assert loop._events == []


def test_the_thread_is_a_daemon_so_it_cannot_outlive_the_process(tmp_path, monkeypatch):
    """_swap_in_history is two atomic renames with a restore on failure, so a thread killed
    at process exit cannot leave a half-written archive behind."""
    from rsched.engine import compaction

    monkeypatch.setattr(compaction, "archive_middle", _fake_archive(
        {"mode": "llm-history", "history_files": 1}, delay=5))
    loop = _loop(tmp_path)
    archival.start(loop, [{"role": "user", "content": "m"}], object(), object(), 1)
    assert loop._archival.thread.daemon is True
    assert isinstance(loop._archival.thread, threading.Thread)
