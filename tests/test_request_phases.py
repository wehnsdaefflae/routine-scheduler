"""Where a slow request's seconds went — before the handler, in it, or after it (F906)."""
from __future__ import annotations

import asyncio
import collections
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from helpers import tmp_server
from rsched.web import api_traces, reqphases, slowsample


def _scope(kind: str = "http") -> dict:
    return {"type": kind, "method": "GET", "path": "/api/health/blocked"}


async def _noop_receive() -> dict:
    return {"type": "http.request", "more_body": False}


class Sent:
    """Collects the ASGI messages a layer passed through, in order."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


async def _respond(scope, receive, send, *, body_chunks: int = 1, hold: float = 0.0):
    """A minimal application: optionally slow, optionally streaming its body."""
    if hold:
        await asyncio.sleep(hold)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    for n in range(body_chunks):
        await send({"type": "http.response.body", "body": b"x",
                    "more_body": n < body_chunks - 1})


# ---- the three spans ---------------------------------------------------------------------

def test_the_three_spans_are_measured_and_add_up_to_the_total():
    """The split is the point: a reader must be able to tell queueing before the handler from
    work after it, which is exactly what one elapsed number cannot say."""
    scope, sent = _scope(), Sent()

    async def app(s, r, snd):
        await _respond(s, r, snd, hold=0.02)

    outer = reqphases.PhaseOuter(reqphases.PhaseInner(app))
    asyncio.run(outer(scope, _noop_receive, sent))
    phases = reqphases.phases_of(scope)
    assert set(phases) >= {"pre_ms", "app_ms", "send_ms", "total_ms", "first_byte_ms"}
    assert phases["app_ms"] >= 15, phases
    assert phases["pre_ms"] + phases["app_ms"] + phases["send_ms"] == pytest.approx(
        phases["total_ms"], abs=1.0)


def test_a_slow_app_is_attributed_to_the_app_and_not_to_the_send():
    scope, sent = _scope(), Sent()

    async def app(s, r, snd):
        await _respond(s, r, snd, hold=0.05)

    asyncio.run(reqphases.PhaseOuter(reqphases.PhaseInner(app))(scope, _noop_receive, sent))
    phases = reqphases.phases_of(scope)
    assert reqphases.dominant(phases) == "app", phases
    assert phases["send_ms"] < phases["app_ms"]


def test_a_slow_middleware_above_the_app_is_attributed_to_pre():
    """The span this exists to expose: time spent before the application is reached at all,
    which every handler-side instrument reports as zero."""
    scope, sent = _scope(), Sent()

    async def app(s, r, snd):
        await _respond(s, r, snd)

    class SlowLayer:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, s, r, snd):
            await asyncio.sleep(0.05)
            await self.inner(s, r, snd)

    stack = reqphases.PhaseOuter(SlowLayer(reqphases.PhaseInner(app)))
    asyncio.run(stack(scope, _noop_receive, sent))
    phases = reqphases.phases_of(scope)
    assert phases["pre_ms"] >= 40, phases
    assert reqphases.dominant(phases) == "pre", phases


def test_a_slow_write_after_the_handler_is_attributed_to_send():
    """A body that trickles out is work AFTER the handler returned, and the first-byte stamp
    separates a slow encode from a slow write."""
    scope, sent = _scope(), Sent()

    async def app(s, r, snd):
        await snd({"type": "http.response.start", "status": 200, "headers": []})
        await snd({"type": "http.response.body", "body": b"x", "more_body": True})
        await asyncio.sleep(0.05)
        await snd({"type": "http.response.body", "body": b"y", "more_body": False})

    class SendOnly:
        """Stands where gzip sits: inside the phase layers, between app_out and last_byte."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, s, r, snd):
            await self.inner(s, r, snd)

    asyncio.run(reqphases.PhaseOuter(SendOnly(app))(scope, _noop_receive, sent))
    stamps = scope[reqphases.KEY]
    assert stamps["last_byte"] - stamps["first_byte"] >= 0.04
    assert reqphases.phases_of(scope)["first_byte_ms"] < reqphases.phases_of(scope)["total_ms"]


# ---- what it must never do --------------------------------------------------------------

@pytest.mark.parametrize("kind", ["websocket", "lifespan"])
def test_a_non_http_scope_is_passed_through_untimed(kind):
    """The live streams are websockets. A layer that stamped them would measure a connection
    that is open for hours as a slow request, and nothing here may touch their scope."""
    scope, sent = _scope(kind), Sent()
    seen = {}

    async def app(s, r, snd):
        seen["scope"] = s
        await snd({"type": "noop"})

    asyncio.run(reqphases.PhaseOuter(reqphases.PhaseInner(app))(scope, _noop_receive, sent))
    assert reqphases.KEY not in scope
    assert seen["scope"] is scope
    assert sent.messages == [{"type": "noop"}]


def test_an_untimed_scope_reads_as_no_phases_rather_than_raising():
    assert reqphases.phases_of({}) is None
    assert reqphases.phases_of({reqphases.KEY: "not a dict"}) is None
    assert reqphases.phases_of({reqphases.KEY: {}}) is None
    assert reqphases.dominant(None) == ""
    assert reqphases.dominant({}) == ""


def test_the_application_still_runs_when_a_stamp_cannot_be_written():
    """A timing instrument that can fail a request is worse than none: the stamp is best-effort
    and the response must be identical without it."""
    scope, sent = _scope(), Sent()
    scope[reqphases.KEY] = {"entry": 0.0}

    class Unwritable(dict):
        def __setitem__(self, key, value):
            raise RuntimeError("no")

    scope[reqphases.KEY] = Unwritable(entry=0.0)

    async def app(s, r, snd):
        await _respond(s, r, snd)

    asyncio.run(reqphases.PhaseInner(app)(scope, _noop_receive, sent))
    assert [m["type"] for m in sent.messages] == ["http.response.start", "http.response.body"]


def test_the_app_span_is_stamped_even_when_the_handler_raises():
    """An exception is a request too, and the layer that was measuring it must not swallow the
    exception or lose the span."""
    scope, sent = _scope(), Sent()

    async def app(s, r, snd):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(reqphases.PhaseOuter(reqphases.PhaseInner(app))(scope, _noop_receive, sent))
    assert "app_out" in scope[reqphases.KEY]


def test_the_durable_row_names_the_dominant_span_and_the_three_numbers():
    """The deque and the log die with the process; this row is what a later run can read, so the
    attribution has to reach it or the instrument answers only for whoever was watching."""
    recorded = []
    state = SimpleNamespace(slow_requests=collections.deque(), server=object())
    entry = {"path": "/api/health/blocked", "method": "GET", "seconds": 2.78, "in_flight": 0,
             "sample": "", "pre_ms": 2600.0, "app_ms": 30.0, "send_ms": 150.0,
             "dominant": "pre"}
    with mock.patch.object(api_traces, "record_server_trace",
                           lambda server, **kw: recorded.append(kw)):
        api_traces.record_slow_request(state, entry)
    detail = recorded[0]["detail"]
    assert "mostly pre" in detail, detail
    for span in ("pre 2600.0ms", "app 30.0ms", "send 150.0ms"):
        assert span in detail, detail


def test_the_added_clause_leaves_both_downstream_readers_matching():
    """`detail` is parsed by anchored regexes in self-audit's trace fold (a leading `<n>ms` and
    `in_flight (\\d+)`), and a row whose shape moved is a fold that silently drops rows."""
    recorded = []
    state = SimpleNamespace(slow_requests=collections.deque(), server=object())
    with mock.patch.object(api_traces, "record_server_trace",
                           lambda server, **kw: recorded.append(kw)):
        api_traces.record_slow_request(state, {
            "path": "/api/items", "method": "GET", "seconds": 7.61, "in_flight": 3,
            "sample": "20261122-170000-001.json", "pre_ms": 10.0, "app_ms": 7500.0,
            "send_ms": 100.0, "dominant": "app"})
    detail = recorded[0]["detail"]
    assert re.match(r"^\d+ms", detail), detail
    assert re.search(r"in_flight (\d+)", detail).group(1) == "3", detail
    assert detail.endswith("sample 20261122-170000-001.json"), detail


def test_a_row_with_no_phases_is_written_exactly_as_before():
    """Every row the self-sample cooldown deduplicated, and every row from a request that was
    never stamped, must keep the old shape — the clause is additive or it is a migration."""
    recorded = []
    state = SimpleNamespace(slow_requests=collections.deque(), server=object())
    with mock.patch.object(api_traces, "record_server_trace",
                           lambda server, **kw: recorded.append(kw)):
        api_traces.record_slow_request(state, {
            "path": "/api/status", "method": "GET", "seconds": 2.1, "in_flight": 0,
            "sample": "", "dominant": ""})
    assert recorded[0]["detail"] == "2100ms, GET, in_flight 0"


def test_the_span_a_basehttp_composer_can_see_is_the_app_span_not_the_pre_span(tmp_path):
    """THE configuration that ships: the row is composed by a `BaseHTTPMiddleware`, whose
    `call_next` returns at `http.response.start` — so `app_out` and `last_byte` are unstamped
    when the row is built. Reading the app's span only from `app_out` left `pre_ms` the one
    known span, and a handler that held the request for seconds was recorded as `mostly pre
    (pre 0.1ms)`. The span to the first byte is what the composer can actually see."""
    app = FastAPI()
    app.state.server = tmp_server(tmp_path)
    # `add_middleware` puts each new layer OUTSIDE the previous, so this is `web/app.py`'s
    # order: the phase layers bracket the stack and the row's composer sits outside them both.
    app.add_middleware(reqphases.PhaseInner)
    app.add_middleware(reqphases.PhaseOuter)
    slowsample.install(app)
    app.state.slow_request_s = 0.05

    @app.get("/slow-handler")
    def slow_handler() -> dict:
        time.sleep(0.2)
        return {"ok": True}

    rows: list[dict] = []
    with mock.patch.object(api_traces, "record_server_trace",
                           lambda server, **kw: rows.append(kw)), TestClient(app) as client:
        assert client.get("/slow-handler").status_code == 200

    assert rows, "a request over the threshold recorded no slow row"
    detail = rows[0]["detail"]
    assert "mostly app" in detail, detail
    assert "mostly pre" not in detail, detail
    entry = app.state.slow_requests[0]
    assert entry["app_ms"] >= 150, entry
    assert entry["pre_ms"] < entry["app_ms"], entry


def test_a_span_too_small_to_explain_the_wait_names_nothing(tmp_path):
    """Attribution is a claim, and the claim has to be checkable against the elapsed time the
    caller measured. Three spans summing to a fraction of a millisecond cannot explain an
    11-second request, and the largest of them is still the largest."""
    phases = {"pre_ms": 0.2, "app_ms": 0.1, "send_ms": 0.0}
    assert reqphases.dominant(phases) == "pre"             # no elapsed given: unchanged
    assert reqphases.dominant(phases, 11_630.0) == ""      # cannot explain it: say nothing
    assert reqphases.dominant({"pre_ms": 9_000.0}, 11_630.0) == "pre"


def test_an_unattributed_row_still_carries_its_numbers(tmp_path):
    """A row whose word was withheld must not read like a row nothing measured: dropping the
    spans with the word is what made the false rows unfalsifiable."""
    recorded = []
    state = SimpleNamespace(slow_requests=collections.deque(), server=object())
    with mock.patch.object(api_traces, "record_server_trace",
                           lambda server, **kw: recorded.append(kw)):
        api_traces.record_slow_request(state, {
            "path": "/api/questions", "method": "GET", "seconds": 11.63, "in_flight": 0,
            "sample": "", "pre_ms": 0.2, "app_ms": 0.1, "first_byte_ms": 11_629.0,
            "dominant": ""})
    detail = recorded[0]["detail"]
    assert "unattributed" in detail, detail
    assert "first_byte 11629.0ms" in detail, detail
    assert re.match(r"^\d+ms", detail), detail


def test_the_row_carries_no_query_string_or_header(tmp_path):
    """F906's own constraint is credential-free telemetry: the phases are numbers, and nothing
    here reads a header, a cookie or a query string."""
    source = Path(reqphases.__file__).read_text(encoding="utf-8")
    for forbidden in ("query_string", "cookie", "authorization", 'scope["headers"]'):
        assert forbidden not in source, f"reqphases reads {forbidden}"
