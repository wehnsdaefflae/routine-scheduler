"""The read-model caching discipline: stat-fingerprint memo + the shared usage-stream
parser. A cache hit must be invisible (equal values, isolated copies); any input change
— append, atomic rewrite, deletion — must miss; a burst of identical misses computes ONCE
(single-flight), and a waiter never accepts a result its sources have outrun."""

import json
import threading

from rsched.readmodels import memo
from rsched.readmodels.usage_stream import usage_records


def test_memoized_hits_until_any_input_changes(tmp_path):
    memo.reset()
    f = tmp_path / "a.jsonl"
    f.write_text("one\n", encoding="utf-8")
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls), "rows": [1, 2]}

    v1 = memo.memoized("k", [f], compute)
    v2 = memo.memoized("k", [f], compute)
    assert v1 == v2 and len(calls) == 1          # hit — compute ran once
    v2["rows"].append(99)                        # a caller mutating its copy…
    assert memo.memoized("k", [f], compute)["rows"] == [1, 2]   # …never poisons the cache
    f.write_text("one\ntwo\n", encoding="utf-8")  # append/size change → miss
    assert memo.memoized("k", [f], compute)["n"] == 2
    f.unlink()                                    # deletion → miss
    assert memo.memoized("k", [f], compute)["n"] == 3


def test_usage_records_parse_once_and_refresh_on_append(tmp_path):
    memo.reset()
    ctrl = tmp_path / ".control"
    ctrl.mkdir(parents=True)
    stream = ctrl / "workflow-usage.jsonl"
    stream.write_text(json.dumps({"routine": "a", "tokens": 5}) + "\nnot json\n",
                      encoding="utf-8")
    first = usage_records(tmp_path)
    assert [r["routine"] for r in first] == ["a"]          # bad line skipped
    assert usage_records(tmp_path) is first                # shared value — no re-parse
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": "b", "tokens": 7}) + "\n")
    assert [r["routine"] for r in usage_records(tmp_path)] == ["a", "b"]
    assert usage_records(tmp_path / "ghost-home") == []    # missing stream → empty


def test_a_burst_of_identical_misses_computes_once(tmp_path):
    memo.reset()
    f = tmp_path / "src.txt"
    f.write_text("v1", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def compute():
        calls.append(1)
        entered.set()
        release.wait(5)
        return {"n": len(calls)}

    results: list[dict] = []
    threads = [threading.Thread(target=lambda: results.append(memo.memoized("burst", [f], compute)))
               for _ in range(6)]
    for t in threads:
        t.start()
    assert entered.wait(5)     # the leader is inside compute; the rest queue on its key…
    release.set()              # …and read its entry instead of walking again
    for t in threads:
        t.join(5)
    assert calls == [1] and results == [{"n": 1}] * 6


def test_a_waiter_rejects_a_result_its_sources_outran(tmp_path):
    """The fingerprint that vouches for a value describes the sources BEFORE that value's
    compute. A waiter re-stats after its wait: if the source moved while the leader was
    computing, the leader's entry is not the waiter's answer."""
    memo.reset()
    f = tmp_path / "src.txt"
    f.write_text("v1", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def compute():
        calls.append(1)
        text = f.read_text(encoding="utf-8")
        if len(calls) == 1:
            entered.set()
            release.wait(5)
        return text

    results: list[str] = []
    leader = threading.Thread(target=lambda: results.append(memo.memoized("k", [f], compute)))
    leader.start()
    assert entered.wait(5)

    # The waiter has to be COMMITTED to the waiting path before the source moves, or this test
    # measures something else and says nothing about it. Starting the thread does not commit it:
    # a waiter descheduled between its entry `fingerprint` and `flight.acquire(blocking=False)`
    # can arrive after the leader released, take the non-blocking acquire, and match the
    # leader's entry with the fingerprint it read BEFORE the write — an ordinary cache hit
    # returning "v1". That is correct behaviour for what actually happened; it just is not the
    # waiter path, so the run silently asserts the wrong thing. It failed that way once in the
    # full suite on 2026-09-20 and passed on every rerun, which is the worst shape a test has.
    #
    # `waited` is decided by exactly one event — that non-blocking acquire returning False — so
    # wrap the flight lock and wait for it. The leader still holds the lock (nothing releases it
    # until `release` is set below), so a committed waiter is guaranteed to block and to re-stat
    # AFTER the write.
    real_flight = memo._flights["k"]
    committed = threading.Event()

    class WatchedFlight:
        def acquire(self, blocking: bool = True) -> bool:
            got = real_flight.acquire(blocking)
            if not blocking and not got:
                committed.set()
            return got

        def release(self) -> None:
            real_flight.release()

        def locked(self) -> bool:
            return real_flight.locked()

    memo._flights["k"] = WatchedFlight()   # type: ignore[assignment]
    waiter = threading.Thread(target=lambda: results.append(memo.memoized("k", [f], compute)))
    waiter.start()
    assert committed.wait(5)               # queued behind the leader, not racing it
    f.write_text("v2", encoding="utf-8")   # the source moves while the leader still computes
    release.set()
    leader.join(5)
    waiter.join(5)
    assert sorted(results) == ["v1", "v2"] and len(calls) == 2
    assert memo.memoized("k", [f], compute) == "v2" and len(calls) == 2   # the fresh entry holds
