"""F871: when a request crosses the slow threshold, the daemon samples ITSELF while it stalls."""

from __future__ import annotations

import asyncio
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
    """A bare app with a routines home and a fast watch — the unit the stall is measured on."""
    app = FastAPI()
    server = tmp_server(tmp_path)
    app.state.server = server
    app.state.slow_request_s = 0.05
    app.state.in_flight = 0
    app.state.slow_watch = SlowWatch(app, tick_s=0.01, cooldown_s=0.0, idle_ticks=4)
    return app, server


def test_the_snapshot_names_the_trigger_both_pools_and_the_stacks(watched):
    """The reading the item exists for: who held the pool while the request waited. Armed ON
    an event loop, as the middleware arms it — only there can anyio's limiter be read; from a
    bare thread the snapshot would hold whatever limiter an earlier test left behind."""
    app, server = watched

    async def armed() -> int:
        return app.state.slow_watch.arm("GET", "/api/health/blocked")

    token = asyncio.run(armed())
    try:
        files = _wait_for_sample(server)
        assert files, "no self-sample was written while a request was over the threshold"
        body = _body(files[0])

        assert body["trigger"]["path"] == "/api/health/blocked"
        assert body["trigger"]["method"] == "GET"
        assert body["trigger"]["elapsed_s"] >= 0.05          # it had crossed the floor
        assert body["slow_request_s"] == 0.05
        assert "STILL in flight" in body["why"]   # the sentence slowsample actually writes

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
    """The sampler's own thread is IN the snapshot it writes."""
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


def _held_watch(tmp_path):
    """Two requests in flight on a watch whose own thread will never take a snapshot (its
    cooldown has just started), so the test takes the one snapshot itself, synchronously."""
    app = FastAPI()
    app.state.server = tmp_server(tmp_path)
    app.state.slow_request_s = 0.0
    watch = SlowWatch(app, tick_s=0.01, cooldown_s=3600.0, idle_ticks=4)
    watch._last = time.monotonic()
    tokens = [watch.arm("GET", p) for p in ("/api/desktops", "/api/questions")]
    live = dict(watch._live)
    return watch, tokens, live


def test_the_victims_are_pointed_before_the_snapshot_appears(tmp_path, monkeypatch):
    """The file is what a reader waits on: a request finishing between the write and the
    pointing would lose the one name its slow-request row carries."""
    watch, tokens, live = _held_watch(tmp_path)
    at_write: list[dict] = []
    real = slowsample.atomic_write_json
    monkeypatch.setattr(slowsample, "atomic_write_json",
                        lambda path, body: (at_write.append(dict(watch._named)),
                                            real(path, body)))
    watch._take(time.monotonic(), live, live)
    assert set(at_write[0]) == set(tokens), at_write
    names = {watch.disarm(t) for t in tokens}
    assert names == {_samples(watch.app.state.server)[0].name}


def test_a_request_not_yet_over_the_floor_points_at_the_snapshot_it_is_listed_in(tmp_path):
    """The snapshot lists every request in flight, and the cooldown takes no second one for a
    request that crosses the floor a tick later — so that one points at this snapshot too."""
    watch, tokens, live = _held_watch(tmp_path)
    first = {tokens[0]: live[tokens[0]]}               # only the first is over the floor
    watch._take(time.monotonic(), first, live)
    name = _samples(watch.app.state.server)[0].name
    assert [watch.disarm(t) for t in tokens] == [name, name]


def test_a_snapshot_that_could_not_be_written_takes_its_pointers_back(tmp_path, monkeypatch):
    watch, tokens, live = _held_watch(tmp_path)

    def refuse(path, body):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(slowsample, "atomic_write_json", refuse)
    watch._take(time.monotonic(), live, live)
    assert [watch.disarm(t) for t in tokens] == ["", ""]


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
