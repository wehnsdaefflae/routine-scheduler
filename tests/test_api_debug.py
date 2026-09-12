"""The daemon's self-observation: thread stacks, threadpool tokens, in-flight and slow
requests — read-only, operator-token only (web/api_debug.py + the timing middleware in app.py)."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="dbg")
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_threads_names_this_process_stacks_and_the_threadpool(client):
    r = client.get("/api/debug/threads")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["in_flight"] >= 1                      # this very request is in flight
    assert body["threadpool"]["total_tokens"] >= 1
    assert body["threadpool"]["borrowed_tokens"] >= 0
    names = {t["name"] for t in body["threads"]}
    assert "MainThread" in names
    # every thread carries frames as "file:line function", innermost last
    for t in body["threads"]:
        assert t["frames"] and all(":" in f for f in t["frames"])


def test_debug_routes_refuse_the_routine_token(tmp_path, make_routine):
    make_routine(slug="dbg2")
    server = make_test_server(tmp_path, routine_token="routine-tok")
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer routine-tok"
        assert c.get("/api/routines").status_code == 200      # the tier reads everything else
        assert c.get("/api/debug/threads").status_code == 403
        assert c.get("/api/debug/slow").status_code == 403


def test_slow_requests_are_recorded_and_logged(client, caplog):
    app = client.app
    app.state.slow_request_s = 0.05                   # the threshold is state, so a test can lower it
    assert client.get("/api/debug/slow").json()["requests"] == []
    with caplog.at_level(logging.WARNING, logger="rsched.web"):
        # a threshold of zero makes any request "slow" — the recording path, not the
        # handler's speed, is what this proves
        app.state.slow_request_s = 0.0
        client.get("/api/lanes")
    slow = client.get("/api/debug/slow").json()
    assert slow["slow_request_s"] == 0.0
    paths = [e["path"] for e in slow["requests"]]
    assert "/api/lanes" in paths
    entry = next(e for e in slow["requests"] if e["path"] == "/api/lanes")
    assert entry["method"] == "GET" and entry["seconds"] >= 0 and "ts" in entry
    assert any("slow request: GET /api/lanes" in rec.getMessage() for rec in caplog.records)
    # the in-flight counter returns to zero once nothing is running
    time.sleep(0.05)
    assert client.get("/api/debug/threads").json()["in_flight"] == 1   # only this request


def test_sse_streams_are_never_counted_as_slow(client):
    """A stream is slow by design; counting it would bury the real entries."""
    app = client.app
    app.state.slow_request_s = 0.0
    r = client.post("/api/sse-ticket")
    assert r.status_code == 200
    # the ticket endpoint is an ordinary request and IS recorded; the two stream paths are
    # exempt — asserted on the predicate the middleware uses, rather than by holding a stream open
    from rsched.web.app import _is_sse_path
    assert _is_sse_path("/api/events")
    assert not _is_sse_path("/api/lanes")
