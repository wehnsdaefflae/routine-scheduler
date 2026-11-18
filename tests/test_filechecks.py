"""F896: concurrency, bounds, row fidelity and failures of the serve-time row scheduler.

Events/barriers prove simultaneous work; timeouts only keep a broken implementation from
hanging the suite. No latency or sleep-only assertion stands in for concurrency.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from rsched.web import filechecks, servable


@pytest.fixture
def checks():
    pool = filechecks.FileChecks(workers=3)
    try:
        yield pool
    finally:
        pool.shutdown(wait=True)


def test_empty_card_submits_no_work(checks, monkeypatch, tmp_path):
    def unexpected(*_args, **_kwargs):
        pytest.fail("an empty card submitted a filesystem check")

    monkeypatch.setattr(checks._pool, "submit", unexpected)
    assert checks.rows([], tmp_path, (tmp_path,)) == []


def test_row_shape_and_predicate_arguments_are_preserved(checks, monkeypatch, tmp_path):
    bases = ["sub/2", "sub/1"]
    source = [{"path": 123, "bases": bases, "reads": 4, "errors": 2, "sub": True},
              {"path": None, "bases": None, "servable": True}]
    before = copy.deepcopy(source)
    roots = (tmp_path,)
    calls = []

    def predicate(path, run_dir, actual_roots, actual_bases):
        calls.append((path, run_dir, actual_roots, actual_bases))
        return path == "123"

    monkeypatch.setattr(servable, "servable_rel", predicate)
    result = checks.rows(source, tmp_path, roots)
    assert result == [{**source[0], "servable": True}, {**source[1], "servable": False}]
    assert source == before                         # memoized rows must never get a verdict
    assert all(row is not original for row, original in zip(result, source, strict=True))
    assert sorted(call[0] for call in calls) == ["", "123"]
    assert all(call[1] == tmp_path and call[2] is roots for call in calls)
    assert next(call[3] for call in calls if call[0] == "123") is bases
    assert next(call[3] for call in calls if call[0] == "") == []


def test_checks_overlap_but_results_keep_first_touched_order(checks, monkeypatch, tmp_path):
    rendezvous = threading.Barrier(3)
    last_done = threading.Event()
    completed = []
    lock = threading.Lock()
    source = [{"path": path} for path in ("first", "middle", "last")]

    def predicate(path, *_args):
        rendezvous.wait(timeout=5)                 # sequential evaluation cannot pass this
        if path == "first":
            assert last_done.wait(5)
        with lock:
            completed.append(path)
        if path == "last":
            last_done.set()
        return path != "middle"

    monkeypatch.setattr(servable, "servable_rel", predicate)
    result = checks.rows(source, tmp_path, (tmp_path,))
    assert completed.index("last") < completed.index("first")
    assert [row["path"] for row in result] == ["first", "middle", "last"]
    assert [row["servable"] for row in result] == [True, False, True]


def test_one_pool_bounds_workers_across_simultaneous_cards(monkeypatch, tmp_path):
    made = []
    real_executor = filechecks.ThreadPoolExecutor

    def executor(**kwargs):
        made.append(kwargs)
        return real_executor(**kwargs)

    monkeypatch.setattr(filechecks, "ThreadPoolExecutor", executor)
    checks = filechecks.FileChecks(workers=3)
    release = threading.Event()
    full = threading.Event()
    windows_submitted = threading.Event()
    start = threading.Barrier(3)
    lock = threading.Lock()
    active = peak = submitted = 0
    real_submit = checks._pool.submit

    def submit(*args, **kwargs):
        nonlocal submitted
        future = real_submit(*args, **kwargs)
        with lock:
            submitted += 1
            if submitted == 6:
                windows_submitted.set()
        return future

    def predicate(_path, *_args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 3:
                full.set()
        try:
            assert release.wait(5)
            return True
        finally:
            with lock:
                active -= 1

    def card(tag):
        start.wait(timeout=5)
        return checks.rows([{"path": f"{tag}-{i}"} for i in range(9)], tmp_path, (tmp_path,))

    monkeypatch.setattr(checks._pool, "submit", submit)
    monkeypatch.setattr(servable, "servable_rel", predicate)
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            futures = [callers.submit(card, tag) for tag in ("a", "b")]
            try:
                start.wait(timeout=5)
                assert full.wait(5)
                assert windows_submitted.wait(5)   # both requests have submitted a full window
                with lock:
                    assert active == peak == 3
            finally:
                release.set()
            assert all(len(future.result(timeout=5)) == 9 for future in futures)
        assert peak == 3
        assert made == [{"max_workers": 3, "thread_name_prefix": "rsched-files"}]
    finally:
        release.set()
        checks.shutdown(wait=True)


def test_pending_submission_window_does_not_grow_with_card(checks, monkeypatch, tmp_path):
    submitted = []
    consumed = []
    peak = 0

    class Result:
        def __init__(self, row):
            self.row = row

        def result(self):
            assert len(submitted) - len(consumed) <= 3
            consumed.append(self.row)
            return {**self.row, "servable": False}

        def cancel(self):
            pytest.fail("a successful card left an unconsumed future")

    def submit(_work, row, *_args):
        nonlocal peak
        submitted.append(row)
        peak = max(peak, len(submitted) - len(consumed))
        return Result(row)

    monkeypatch.setattr(checks._pool, "submit", submit)
    source = [{"path": str(i)} for i in range(100)]
    assert checks.rows(source, tmp_path, ()) == [{**row, "servable": False} for row in source]
    assert consumed == source and peak == 3


def test_first_input_error_wins_even_if_later_error_finishes_first(checks, monkeypatch,
                                                                tmp_path):
    later_failed = threading.Event()
    first_error = RuntimeError("first row failed")
    later_error = ValueError("later row failed")

    def predicate(path, *_args):
        if path == "first":
            assert later_failed.wait(5)
            raise first_error
        if path == "later":
            later_failed.set()
            raise later_error
        return False

    monkeypatch.setattr(servable, "servable_rel", predicate)
    with pytest.raises(RuntimeError) as raised:
        checks.rows([{"path": "first"}, {"path": "later"}], tmp_path, ())
    assert raised.value is first_error              # no wrapping with path/root details
    assert checks.rows([{"path": "fresh"}], tmp_path, ()) == [
        {"path": "fresh", "servable": False}]        # a failed card does not poison the pool


def test_failure_cancels_queued_work_without_submitting_the_tail(checks, monkeypatch, tmp_path):
    error = RuntimeError("failed row")
    submitted = []

    def submit(*_args):
        future = Future()
        if not submitted:
            future.set_exception(error)
        submitted.append(future)
        return future

    monkeypatch.setattr(checks._pool, "submit", submit)
    with pytest.raises(RuntimeError) as raised:
        checks.rows([{"path": str(i)} for i in range(20)], tmp_path, ())
    assert raised.value is error
    assert len(submitted) == 3
    assert all(future.cancelled() for future in submitted[1:])


def test_bad_row_beyond_the_initial_window_is_not_silently_skipped(monkeypatch, tmp_path):
    checks = filechecks.FileChecks(workers=1)
    monkeypatch.setattr(servable, "servable_rel", lambda *_args: True)
    try:
        with pytest.raises(TypeError):
            checks.rows([{"path": "ok"}, None, {"path": "tail"}], tmp_path, ())
    finally:
        checks.shutdown(wait=True)
