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


def test_a_recompute_is_a_use_and_evicts_nothing_for_itself(tmp_path, monkeypatch):
    """A dict keeps an existing key's position on assignment, so a key whose sources moved was
    re-stored IN PLACE — at the eviction end it had reached — and a full cache first evicted a
    bystander for room the key already held. Two inserts later the value just computed was the
    one thrown out."""
    memo.reset()
    monkeypatch.setattr(memo, "_MAX_ENTRIES", 3)
    sources = {k: tmp_path / k for k in "abcde"}
    for p in sources.values():
        p.write_text("v1", encoding="utf-8")
    computed: list[str] = []

    def read(key: str) -> str:
        return memo.memoized(key, [sources[key]], lambda: (computed.append(key), key)[1])

    for key in "abc":
        read(key)                                      # full: a oldest, then b, then c
    sources["b"].write_text("v2!", encoding="utf-8")   # b's source moves…
    read("b")                                          # …so b is recomputed: a USE of b
    assert set(memo._cache) == set("abc"), "the recompute evicted a bystander"
    read("d")
    read("e")                                          # evict the two least recent: a, c
    computed.clear()
    read("b")
    assert computed == [], "the value just recomputed was evicted as if it were the oldest"


def test_a_run_trees_transcripts_are_its_numbered_levels_and_nothing_else(tmp_path):
    """The fingerprint behind the rail's polled read models. A `sub/**` glob walked every
    directory under `sub/` on each poll — a child's artifacts and clones included — and took
    any file there named transcript.jsonl as one of the run's. The tree is the numbered child
    dirs, at any depth, in creation order."""
    run = tmp_path / "run"
    for level in ("sub/2", "sub/2/sub/3", "sub/10"):
        (run / level).mkdir(parents=True)
    stray = run / "sub" / "2" / "artifacts" / "copied-run"
    stray.mkdir(parents=True)
    (stray / "transcript.jsonl").write_text("{}\n", encoding="utf-8")

    assert memo.run_tree(run) == [run, run / "sub/2", run / "sub/2/sub/3", run / "sub/10"]
    paths = memo.transcript_paths(run)
    assert stray / "transcript.jsonl" not in paths
    assert paths[:2] == [run / "transcript.jsonl", run / "transcript.jsonl.gz"]
    assert run / "sub/2/sub/3/transcript.jsonl.gz" in paths


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
