"""F871: when a request crosses the slow threshold, the daemon samples ITSELF while it stalls.

The console's 10-30 s waits are a blocked process, not slow handlers: `/api/llm-tasks` — an
`async def` returning an in-memory snapshot and doing no I/O — recorded a worst of 34,396 ms,
and unrelated endpoints finished in the same second to within 20 ms. The case no existing
evidence explains is the QUIET one: 33 of 79 server-side slow rows had `in_flight = 0` and
still took a median 2,700 ms, 24 of them on `/api/health/blocked`, whose handler measures
1.4 ms warm. `in_flight` cannot narrow it by construction (an http middleware never sees a
websocket scope), and `/api/debug/threads` can — but only while somebody is holding a browser
open inside the stall, which at 18:07 nobody is.

So the reading is taken by the daemon, from its own OS thread, WHILE the request is still in
flight. The thread is the design: anything scheduled on the event loop cannot run during a
blocked loop, so it would sample an already-drained pool — the 2026-09-12 misreading with a
timer attached.

What is pinned here is the snapshot's CONTENT, not that a write happened: a test whose subject
is a side effect passes vacuously on an empty file.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

from conftest import authed_client, make_test_server
from helpers import tmp_server
from rsched.web import slowsample
from rsched.web.api_traces import traces_dir
from rsched.web.slowsample import SlowWatch


def _samples(server) -> list[Path]:
    return sorted((Path(server.routines_home) / ".control" / slowsample.SAMPLES_DIR)
                  .glob("*.json"))


def _wait_for_sample(server, *, count: int = 1, deadline_s: float = 5.0) -> list[Path]:
    """Poll until `count` snapshots exist. A deadline, never a bare sleep: the watcher runs on
    its own clock and a fixed sleep is either flaky or slow."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        files = _samples(server)
        if len(files) >= count:
            return files
        time.sleep(0.01)
    return _samples(server)


def _body(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def watched(tmp_path):
    """A bare app with a routines home and a fast watch — the unit the stall is measured on.

    `create_app` is not needed to prove the sampler: what it samples is the PROCESS, and the
    middleware's own arming is proven end-to-end in the HTTP test below.
    """
    app = FastAPI()
    server = tmp_server(tmp_path)
    app.state.server = server
    app.state.slow_request_s = 0.05
    app.state.in_flight = 0
    app.state.slow_watch = SlowWatch(app, tick_s=0.01, cooldown_s=0.0, idle_ticks=4)
    return app, server


def test_the_snapshot_names_the_trigger_both_pools_and_the_stacks(watched):
    """The reading the item exists for: who held the pool while the request waited.

    Both pools, because reading anyio's limiter alone during the 2026-09-12 stall was reading a
    nearly idle pool while the loop's default executor was saturated — `api_debug` keeps them
    together and this is the same reader.
    """
    app, server = watched
    token = app.state.slow_watch.arm("GET", "/api/health/blocked")
    try:
        files = _wait_for_sample(server)
        assert files, "no self-sample was written while a request was over the threshold"
        body = _body(files[0])

        assert body["trigger"]["path"] == "/api/health/blocked"
        assert body["trigger"]["method"] == "GET"
        assert body["trigger"]["elapsed_s"] >= 0.05          # it had crossed the floor
        assert body["slow_request_s"] == 0.05
        assert "still in flight" in body["why"]

        # BOTH pools, by the names `/api/debug/threads` reports them under
        report = body["report"]
        assert set(report["threadpool"]) >= {"total_tokens", "borrowed_tokens"}
        assert set(report["io_pool"]) >= {"installed", "workers", "threads", "queued"}
        assert report["threadpool"]["total_tokens"] >= 1

        # and the stacks, as "file:line function" — the thing that NAMES the holder
        frames = [f for t in report["threads"] for f in t["frames"]]
        assert frames and all(re.search(r":\d+ \S", f) for f in frames)
        assert any(t["name"] == "MainThread" for t in report["threads"])
    finally:
        app.state.slow_watch.disarm(token)


def test_the_reading_is_taken_off_loop_while_the_request_is_still_waiting(watched):
    """The sampler's own thread is IN the snapshot it writes.

    That is the proof the reading was taken during the stall rather than after it: the walker
    can only see itself mid-walk. A loop-scheduled sampler could not have produced this row at
    all, because a blocked loop runs no callbacks.
    """
    app, server = watched
    token = app.state.slow_watch.arm("GET", "/api/llm-tasks")
    try:
        files = _wait_for_sample(server)
        assert files
        body = _body(files[0])
        names = {t["name"] for t in body["report"]["threads"]}
        assert "rsched-slow-watch" in names, names
        # the request it names is still open — this is a snapshot of a LIVE stall
        assert [r["path"] for r in body["waiting"]] == ["/api/llm-tasks"]
    finally:
        app.state.slow_watch.disarm(token)


def test_a_request_under_the_threshold_is_never_sampled(watched):
    """The snapshot has to MEAN "this was slow", or it is noise with a stack attached."""
    app, server = watched
    app.state.slow_request_s = 30.0
    token = app.state.slow_watch.arm("GET", "/api/lanes")
    time.sleep(0.1)                       # several ticks at tick_s=0.01
    app.state.slow_watch.disarm(token)
    assert _samples(server) == []


def test_one_stall_leaves_one_snapshot_and_every_victim_points_at_it(tmp_path):
    """The measured failure is SHARED — three pairs of unrelated endpoints finished within
    20 ms of each other — so sampling per victim would multiply the frame walk by the size of
    the pile-up exactly when the process can least afford it. One sample; all of them point at
    it, so no evidence is lost.
    """
    app = FastAPI()
    server = tmp_server(tmp_path)
    app.state.server = server
    app.state.slow_request_s = 0.05
    app.state.slow_watch = SlowWatch(app, tick_s=0.01, cooldown_s=60.0, idle_ticks=4)
    tokens = [app.state.slow_watch.arm("GET", p)
              for p in ("/api/desktops", "/api/llm-tasks", "/api/questions")]
    try:
        files = _wait_for_sample(server)
        assert len(files) == 1, files
        body = _body(files[0])
        # the snapshot names every request waiting at that instant, longest wait first
        assert {r["path"] for r in body["waiting"]} == {"/api/desktops", "/api/llm-tasks",
                                                       "/api/questions"}
        waits = [r["elapsed_s"] for r in body["waiting"]]
        assert waits == sorted(waits, reverse=True)
    finally:
        pointed = [app.state.slow_watch.disarm(t) for t in tokens]
    assert pointed == [files[0].name] * 3, pointed
    assert len(_samples(server)) == 1


def test_the_snapshot_ring_is_bounded(tmp_path):
    """It fires on an already-slow request, so a daemon stalling all night must not fill the
    disk — the deque it sits beside is bounded for the same reason."""
    app = FastAPI()
    server = tmp_server(tmp_path)
    app.state.server = server
    app.state.slow_request_s = 0.0
    watch = SlowWatch(app, tick_s=0.01, cooldown_s=0.0, keep=3, idle_ticks=4)
    app.state.slow_watch = watch
    seen: list[str] = []
    for _ in range(6):
        token = watch.arm("GET", "/api/questions")
        # Each pass must produce a NEW snapshot, or this test proves nothing about the ring.
        # Polling the file COUNT cannot say so once the ring is full — it stops growing at
        # `keep` — so the wait is for a name this loop has not seen before.
        end = time.monotonic() + 5.0
        fresh: set[str] = set()
        while time.monotonic() < end and not fresh:
            fresh = {p.name for p in _samples(server)} - set(seen)
            if not fresh:
                time.sleep(0.01)
        name = watch.disarm(token)
        assert fresh == {name}, (fresh, name, seen)
        seen.append(name)
    assert len(seen) == 6                        # six stalls really were sampled
    kept = [p.name for p in _samples(server)]
    assert len(kept) == 3, kept                  # …and the ring kept only the newest three
    assert kept == seen[-3:], (kept, seen)       # the OLDEST were the ones pruned


def test_the_watcher_retires_when_nothing_is_in_flight(watched):
    """One thread for the whole app, started on demand: a process that is never slow carries
    no extra thread, and the fast suite does not accumulate one per app."""
    app, _server = watched
    before = {t.name for t in threading.enumerate()}
    token = app.state.slow_watch.arm("GET", "/api/lanes")
    assert any(t.name == "rsched-slow-watch" for t in threading.enumerate())
    app.state.slow_watch.disarm(token)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and any(t.name == "rsched-slow-watch"
                                         for t in threading.enumerate()):
        time.sleep(0.01)
    assert {t.name for t in threading.enumerate()} - before == set()


def test_the_request_path_fails_open_without_a_watch():
    """Engine code on a hot path reads context with `getattr` and fails open: anything the
    request path reaches before every call is handed stand-ins by design in tests."""
    class Bare:
        pass

    assert slowsample.arm(Bare(), "GET", "/api/x") is None
    assert slowsample.disarm(Bare(), None) == ""
    assert slowsample.disarm(Bare(), 7) == ""


def test_a_watch_with_nowhere_to_write_records_nothing_and_raises_nothing(tmp_path):
    """A stand-in server with no routines home must not take the request down with it."""
    app = FastAPI()
    app.state.server = object()           # no `routines_home`
    app.state.slow_request_s = 0.0
    watch = SlowWatch(app, tick_s=0.01, cooldown_s=0.0, idle_ticks=4)
    app.state.slow_watch = watch
    token = watch.arm("GET", "/api/questions")
    time.sleep(0.1)
    assert watch.disarm(token) == ""      # no snapshot, no exception


# ---- end to end, through the real app and its middleware ---------------------------------


def test_a_slow_request_row_points_at_the_snapshot_taken_while_it_ran(tmp_path, make_routine):
    """The durable row and the snapshot are joined, so the morning-after question is answerable
    from the store alone: which endpoint, how long, under what load, and WHO HELD THE POOL.
    """
    make_routine(slug="smpl")
    server = make_test_server(tmp_path)
    with authed_client(server) as c:
        app = c.app
        app.state.slow_request_s = 0.0
        app.state.slow_watch = SlowWatch(app, tick_s=0.01, cooldown_s=0.0, idle_ticks=4)

        @app.get("/api/zz-stall")
        def _stall() -> dict:                       # a sync def, like the four worst endpoints
            time.sleep(0.2)
            return {"ok": True}

        assert c.get("/api/zz-stall").status_code == 200

        files = _wait_for_sample(app.state.server)
        assert files, "the middleware armed no sample for a request over the threshold"
        name = files[0].name

        rows = [json.loads(ln)
                for p in sorted(traces_dir(app.state.server).glob("*.jsonl"))
                for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        mine = [r for r in rows if r.get("kind") == "slow" and r.get("target") == "/api/zz-stall"]
        assert mine, rows
        detail = mine[0]["detail"]
        assert f"sample {name}" in detail, detail
        # and the two readers of this field still match it (self-audit's `_ms`, `_in_flight`)
        assert re.match(r"\d+ms,", detail), detail
        assert re.search(r"in_flight (\d+)", detail), detail

        body = _body(files[0])
        assert body["trigger"]["path"] == "/api/zz-stall"
        assert any(t["name"] == "rsched-slow-watch" for t in body["report"]["threads"])


def test_the_sample_pointer_is_absent_when_no_snapshot_was_taken(tmp_path, make_routine):
    """Every row the cooldown deduplicated is written exactly as before — the pointer is an
    addition, never a new required field."""
    make_routine(slug="smpl2")
    with authed_client(make_test_server(tmp_path)) as c:
        app = c.app
        app.state.slow_request_s = 0.0
        app.state.slow_watch = None            # nothing armed, nothing sampled
        assert c.get("/api/lanes").status_code == 200
        rows = [json.loads(ln)
                for p in sorted(traces_dir(app.state.server).glob("*.jsonl"))
                for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        mine = [r for r in rows if r.get("target") == "/api/lanes"]
        assert mine and "sample" not in mine[0]["detail"], mine
        assert re.match(r"\d+ms,", mine[0]["detail"])
