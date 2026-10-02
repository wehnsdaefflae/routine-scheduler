"""Live streams: the run-tail generators (header + incremental appends + state + end) unit-tested
directly, the socket pump (frames, keepalive, teardown) against a stand-in socket, and the two
WebSocket endpoints (/api/events, /api/runs/{id}/events) through the app for the frame contract
and the single-use stream ticket that admits a socket (the WebSocket API sends no headers)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import mk_run
from rsched.daemon.events import EventBus
from rsched.paths import atomic_write_json
from rsched.web import streams

TS = "20260710-120000"


def _append_line(path, obj):
    # sync helper: keeps blocking file IO out of the async test bodies (ASYNC230)
    with path.open("a") as fh:
        fh.write(json.dumps(obj) + "\n")


def _mk_run(routines, slug, ts, state):
    return mk_run(routines / slug, ts, state, turn=1, transcript=[
        {"type": "header", "run_id": f"{slug}:{ts}"},
        {"ts": "t", "type": "assistant_action", "turn": 1,
         "payload": {"say": "s", "kind": "util", "name": "gu-list"}}])


def _label(item: dict) -> tuple:
    data = item["data"]
    return item["event"], data.get("type") or data.get("state")


# ---------------------------------------------------------------- generators


async def test_run_stream_tails_appends_then_ends(tmp_path, monkeypatch):
    """The live tail: replays existing lines, picks up lines appended mid-stream, emits a
    state event per change and exactly one final end event."""
    monkeypatch.setattr(streams, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "running")
    gen = streams.run_stream(run_dir)
    first = [await asyncio.wait_for(anext(gen), 2) for _ in range(3)]
    assert [_label(i) for i in first] == [("transcript", "header"),
                                          ("transcript", "assistant_action"),
                                          ("state", "running")]
    # append while the stream is live — the tail must deliver it before ending
    _append_line(run_dir / "transcript.jsonl",
                 {"ts": "t", "type": "finish", "turn": 2, "payload": {"status": "ok"}})
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "finished", "turn": 2})
    rest = []
    while True:
        try:
            rest.append(await asyncio.wait_for(anext(gen), 2))
        except StopAsyncIteration:
            break
    labels = [_label(i) for i in rest]
    assert ("transcript", "finish") in labels
    assert ("state", "finished") in labels
    assert labels[-1] == ("end", "finished") and labels.count(("end", "finished")) == 1


async def test_run_stream_emits_state_event_on_phase_change(tmp_path, monkeypatch):
    """A phase transition (same run state) fires its own state event carrying `phase` —
    the UI's state-graph diagram updates on it."""
    monkeypatch.setattr(streams, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "running")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "running", "phase": "orient"})
    gen = streams.run_stream(run_dir)
    first = [await asyncio.wait_for(anext(gen), 2) for _ in range(3)]
    assert first[-1]["event"] == "state" and first[-1]["data"]["phase"] == "orient"
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "running", "phase": "measure"})
    nxt = await asyncio.wait_for(anext(gen), 2)
    assert nxt["event"] == "state" and nxt["data"]["phase"] == "measure"
    await gen.aclose()


async def test_run_stream_emits_state_event_on_new_question_same_state(tmp_path, monkeypatch):
    """A NEW pending question (new qid) with the SAME run state + phase still fires its own
    state event (F93). The run-page question form re-renders only on a `state` event, so a
    clarify run that answers one question and re-asks the next within the same phase must
    still push the new question — otherwise an open run page keeps showing the stale form."""
    monkeypatch.setattr(streams, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "waiting_user")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "waiting_user", "phase": "clarify",
                       "question": {"qid": "q-a", "question": "First?"}})
    gen = streams.run_stream(run_dir)
    first = [await asyncio.wait_for(anext(gen), 2) for _ in range(3)]
    assert first[-1]["event"] == "state"
    assert first[-1]["data"]["question"]["qid"] == "q-a"
    # same state + phase, different question — must NOT be coalesced away
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "waiting_user", "phase": "clarify",
                       "question": {"qid": "q-b", "question": "Second?"}})
    nxt = await asyncio.wait_for(anext(gen), 2)
    assert nxt["event"] == "state" and nxt["data"]["question"]["qid"] == "q-b"
    await gen.aclose()


async def test_run_stream_start_offset_skips_replay(tmp_path, monkeypatch):
    """A reconnecting client passes its offset and gets only what it has not seen."""
    monkeypatch.setattr(streams, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "finished")
    header_len = len((run_dir / "transcript.jsonl").read_bytes().splitlines(keepends=True)[0])
    events = [item async for item in streams.run_stream(run_dir, start_offset=header_len)]
    kinds = [_label(i) for i in events]
    assert ("transcript", "header") not in kinds          # already seen before reconnect
    assert ("transcript", "assistant_action") in kinds
    assert kinds[-1] == ("end", "finished")


async def test_bus_stream_delivers_published_events():
    bus = EventBus()
    gen = streams.bus_stream(bus)
    task = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)                      # let the generator subscribe
    bus.publish({"event": "run_started", "run_id": f"apir:{TS}"})
    item = await asyncio.wait_for(task, 2)
    assert item == {"event": "bus", "data": {"event": "run_started", "run_id": f"apir:{TS}"}}
    await gen.aclose()
    assert not bus._subscribers                 # closing the stream unsubscribes


async def test_a_publish_from_a_worker_thread_wakes_the_subscriber_at_once():
    """Sync routes and the LLM task center publish from worker threads. An asyncio.Queue
    put from there neither locks against the loop nor wakes it, so a waiting stream heard
    about the event only when something else next woke the loop — here, the 3 s timeout.
    `publish` marshals onto the subscriber's loop and the wake is immediate."""
    import threading
    import time

    bus = EventBus()
    with bus.subscribe() as q:
        waiter = asyncio.ensure_future(q.get())
        await asyncio.sleep(0)                  # the getter is parked on the queue
        t0 = time.monotonic()
        def later() -> None:                    # once the loop is asleep in its selector
            time.sleep(0.2)
            bus.publish({"event": "question_answered"})

        threading.Thread(target=later).start()
        got = await asyncio.wait_for(waiter, 3)
        assert got == {"event": "question_answered"}
        assert time.monotonic() - t0 < 1.0


# ---------------------------------------------------------------- the pump


class _Socket:
    """The three calls `pump` makes on a WebSocket, recorded. `leave()` is the browser going."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed: int | None = None
        self._gone = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    async def receive(self) -> dict:
        await self._gone.wait()
        return {"type": "websocket.disconnect", "code": 1001}

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    def leave(self) -> None:
        self._gone.set()


async def test_pump_sends_each_item_as_one_frame_and_closes_when_the_stream_ends():
    async def two():
        yield {"event": "transcript", "data": {"type": "header"}}
        yield {"event": "end", "data": {"state": "finished"}}

    ws = _Socket()
    await asyncio.wait_for(streams.pump(ws, two()), 2)
    assert ws.frames == [{"event": "transcript", "data": {"type": "header"}},
                         {"event": "end", "data": {"state": "finished"}}]
    assert ws.closed == 1000


async def test_pump_pings_an_idle_socket(monkeypatch):
    """A run parked on a question can stay silent for hours; the ping is what lets the
    console tell a quiet stream from a dead one (static/api.js STALE_MS)."""
    monkeypatch.setattr(streams, "KEEPALIVE_S", 0.05)
    bus = EventBus()
    ws = _Socket()
    task = asyncio.ensure_future(streams.pump(ws, streams.bus_stream(bus)))
    for _ in range(100):
        if ws.frames:
            break
        await asyncio.sleep(0.02)
    assert ws.frames and ws.frames[0] == {"event": "ping"}
    ws.leave()
    await asyncio.wait_for(task, 2)


async def test_a_departed_peer_ends_the_stream_and_releases_its_subscription():
    """The console closing a tab must free the bus subscription NOW, not at garbage
    collection — a subscription nobody reads keeps filling its queue."""
    bus = EventBus()
    ws = _Socket()
    task = asyncio.ensure_future(streams.pump(ws, streams.bus_stream(bus)))
    for _ in range(100):
        if bus._subscribers:
            break
        await asyncio.sleep(0.01)
    assert bus._subscribers
    ws.leave()
    await asyncio.wait_for(task, 2)
    assert not bus._subscribers
    assert ws.closed is None                    # nobody is left to receive a close


async def test_a_failed_stream_closes_1011():
    async def broken():
        yield {"event": "transcript", "data": {}}
        raise RuntimeError("disk gone")

    ws = _Socket()
    await asyncio.wait_for(streams.pump(ws, broken()), 2)
    assert ws.closed == 1011


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def client(api_client, make_routine, monkeypatch):
    monkeypatch.setattr(streams, "POLL_S", 0.01)
    make_routine(slug="apir")
    return api_client


def _ticket(c) -> str:
    return c.post("/api/stream-ticket").json()["ticket"]


def _frames_until_close(ws) -> tuple[list[dict], int]:
    frames = []
    while True:
        try:
            frames.append(ws.receive_json())
        except WebSocketDisconnect as exc:
            return frames, exc.code


def test_run_events_socket_frame_contract(client):
    c, tmp = client
    _mk_run(tmp / "routines", "apir", TS, "finished")
    with c.websocket_connect(f"/api/runs/apir:{TS}/events?ticket={_ticket(c)}") as ws:
        frames, code = _frames_until_close(ws)
    assert (frames[0]["event"], frames[0]["data"]["type"]) == ("transcript", "header")
    assert ("transcript", "assistant_action") in [(f["event"], f["data"].get("type"))
                                                  for f in frames]
    assert ("state", "finished") in [(f["event"], f["data"].get("state")) for f in frames]
    assert frames[-1] == {"event": "end", "data": {"state": "finished"}}
    assert code == 1000


def test_a_socket_needs_a_ticket_and_a_ticket_opens_one_socket(client, monkeypatch):
    """No ticket, a bogus one, an expired one and a SPENT one are all refused 1008 — a ticket
    rides the URL, so one that leaked must not open anything a second time."""
    c, _ = client

    async def one_bus_event(bus):
        yield streams._event("bus", {"event": "run_started", "run_id": f"apir:{TS}"})

    monkeypatch.setattr(streams, "bus_stream", one_bus_event)
    bare = TestClient(c.app)                    # no Authorization header

    def refused(url: str) -> int:
        with bare.websocket_connect(url) as ws:
            return _frames_until_close(ws)[1]

    assert refused("/api/events") == streams.CLOSE_UNAUTHORIZED
    assert refused("/api/events?ticket=bogus") == streams.CLOSE_UNAUTHORIZED
    ticket = _ticket(c)
    with bare.websocket_connect(f"/api/events?ticket={ticket}") as ws:
        frames, code = _frames_until_close(ws)
    assert frames == [{"event": "bus",
                       "data": {"event": "run_started", "run_id": f"apir:{TS}"}}]
    assert code == 1000
    assert refused(f"/api/events?ticket={ticket}") == streams.CLOSE_UNAUTHORIZED   # spent
    stale = _ticket(c)
    c.app.state.stream_tickets[stale] = 0.0     # fast-forward: long expired
    assert refused(f"/api/events?ticket={stale}") == streams.CLOSE_UNAUTHORIZED


def test_an_unknown_run_closes_gone_only_after_admission(client):
    """Admission comes first: without a ticket the caller cannot even learn the run is
    missing; with one, the socket says CLOSE_GONE, the console's 404."""
    c, _ = client
    url = "/api/runs/ghost:00000000-000000/events"
    with c.websocket_connect(url) as ws:
        assert _frames_until_close(ws)[1] == streams.CLOSE_UNAUTHORIZED
    with c.websocket_connect(f"{url}?ticket={_ticket(c)}") as ws:
        assert _frames_until_close(ws)[1] == streams.CLOSE_GONE
