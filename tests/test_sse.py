"""SSE: the run-tail generators (header + incremental appends + state + end) unit-tested
directly, and the three endpoints (/api/events, /api/runs/{id}/events,
/api/wizard/{wid}/events) through the app for sse-starlette wire format and the
token-in-query auth that native EventSource clients rely on.

The endpoint tests use runs that are already terminal: TestClient buffers a response to
completion, so only a stream that ends can be asserted over HTTP."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from rsched.daemon.events import EventBus
from rsched.paths import atomic_write_json
from rsched.web import sse

TS = "20260710-120000"


def _append_line(path, obj):
    # sync helper: keeps blocking file IO out of the async test bodies (ASYNC230)
    with path.open("a") as fh:
        fh.write(json.dumps(obj) + "\n")


def _mk_run(routines, slug, ts, state):
    run_dir = routines / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": state, "turn": 1})
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": f"{slug}:{ts}"}) + "\n"
        + json.dumps({"ts": "t", "type": "assistant_action", "turn": 1,
                      "payload": {"say": "s", "kind": "util", "name": "gu-list"}}) + "\n")
    return run_dir


def _label(item: dict) -> tuple:
    data = json.loads(item["data"])
    return item["event"], data.get("type") or data.get("state")


# ---------------------------------------------------------------- generators


async def test_run_stream_tails_appends_then_ends(tmp_path, monkeypatch):
    """The live tail: replays existing lines, picks up lines appended mid-stream, emits a
    state event per change and exactly one final end event."""
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "running")
    gen = sse.run_stream(run_dir)
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
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "running")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "running", "phase": "orient"})
    gen = sse.run_stream(run_dir)
    first = [await asyncio.wait_for(anext(gen), 2) for _ in range(3)]
    state_ev = json.loads(first[-1]["data"])
    assert first[-1]["event"] == "state" and state_ev["phase"] == "orient"
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "running", "phase": "measure"})
    nxt = await asyncio.wait_for(anext(gen), 2)
    assert nxt["event"] == "state" and json.loads(nxt["data"])["phase"] == "measure"
    await gen.aclose()


async def test_run_stream_emits_state_event_on_new_question_same_state(tmp_path, monkeypatch):
    """A NEW pending question (new qid) with the SAME run state + phase still fires its own
    state event (F93). The run-page question form re-renders only on a `state` event, so a
    clarify run that answers one question and re-asks the next within the same phase must
    still push the new question — otherwise an open run page keeps showing the stale form."""
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "waiting_user")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "waiting_user", "phase": "clarify",
                       "question": {"qid": "q-a", "question": "First?"}})
    gen = sse.run_stream(run_dir)
    first = [await asyncio.wait_for(anext(gen), 2) for _ in range(3)]
    assert first[-1]["event"] == "state"
    assert json.loads(first[-1]["data"])["question"]["qid"] == "q-a"
    # same state + phase, different question — must NOT be coalesced away
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"apir:{TS}", "state": "waiting_user", "phase": "clarify",
                       "question": {"qid": "q-b", "question": "Second?"}})
    nxt = await asyncio.wait_for(anext(gen), 2)
    assert nxt["event"] == "state" and json.loads(nxt["data"])["question"]["qid"] == "q-b"
    await gen.aclose()


async def test_run_stream_start_offset_skips_replay(tmp_path, monkeypatch):
    """A reconnecting client passes its offset and gets only what it has not seen."""
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    run_dir = _mk_run(tmp_path, "apir", TS, "finished")
    header_len = len((run_dir / "transcript.jsonl").read_bytes().splitlines(keepends=True)[0])
    events = [item async for item in sse.run_stream(run_dir, start_offset=header_len)]
    kinds = [_label(i) for i in events]
    assert ("transcript", "header") not in kinds          # already seen before reconnect
    assert ("transcript", "assistant_action") in kinds
    assert kinds[-1] == ("end", "finished")


async def test_bus_stream_delivers_published_events():
    bus = EventBus()
    gen = sse.bus_stream(bus)
    task = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)                      # let the generator subscribe
    bus.publish({"event": "run_started", "run_id": f"apir:{TS}"})
    item = await asyncio.wait_for(task, 2)
    assert item["event"] == "bus"
    assert json.loads(item["data"]) == {"event": "run_started", "run_id": f"apir:{TS}"}
    await gen.aclose()
    assert not bus._subscribers                 # closing the stream unsubscribes


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def client(api_client, make_routine, monkeypatch):
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    make_routine(slug="apir")
    return api_client


def _wire_events(text: str) -> list[tuple[str, dict]]:
    """Parse SSE wire text (`event:`/`data:` lines, comment lines ignored) — what a
    standard EventSource client sees."""
    out, event = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event:
            out.append((event, json.loads(line.split(":", 1)[1])))
    return out


def test_run_events_endpoint_wire_contract(client):
    c, tmp = client
    _mk_run(tmp / "routines", "apir", TS, "finished")
    r = c.get(f"/api/runs/apir:{TS}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    pairs = _wire_events(r.text)
    assert (pairs[0][0], pairs[0][1]["type"]) == ("transcript", "header")
    assert ("transcript", "assistant_action") in [(e, d.get("type")) for e, d in pairs]
    assert ("state", "finished") in [(e, d.get("state")) for e, d in pairs]
    assert pairs[-1] == ("end", {"state": "finished"})


def test_wizard_events_endpoint_same_contract(client):
    c, tmp = client
    wid = f".wizard-{TS}"
    _mk_run(tmp / "routines", wid, TS, "finished")
    r = c.get(f"/api/wizard/{wid}/events")
    assert r.status_code == 200
    pairs = _wire_events(r.text)
    assert (pairs[0][0], pairs[0][1]["type"]) == ("transcript", "header")
    assert pairs[-1] == ("end", {"state": "finished"})


def test_bus_endpoint_wire_and_sse_ticket(client, monkeypatch):
    """/api/events wiring: 401 without credentials, streams with a short-lived ?ticket=
    (EventSource cannot send headers; the bearer token never rides the query string). A
    finite stand-in generator lets the response complete — the real bus_stream never ends,
    which a buffering TestClient cannot consume."""
    c, _ = client

    async def one_bus_event(bus):
        yield sse._event("bus", {"event": "run_started", "run_id": f"apir:{TS}"})

    monkeypatch.setattr(sse, "bus_stream", one_bus_event)
    bare = TestClient(c.app)                    # no Authorization header
    assert bare.get("/api/events").status_code == 401
    ticket = c.post("/api/sse-ticket").json()["ticket"]
    r = bare.get(f"/api/events?ticket={ticket}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert ("bus", {"event": "run_started", "run_id": f"apir:{TS}"}) in _wire_events(r.text)
