"""An app `create_app` built is FREED once nothing holds it (web/api_core.py).

FastAPI classifies endpoint and dependency callables through process-lifetime `lru_cache`s keyed
on the callable, so a route handler defined as a closure inside `create_app` pinned its whole
app (~5 MB) for the life of the process. The daemon builds one app; the suites build one per
test, and on 2026-11-16 the browser suite held 40 apps after 40 tests and every worker was
OOM-killed near the end of the run. These pins fail on any handler or dependency that closes
over an app again.
"""

from __future__ import annotations

import gc
import weakref

from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched.web.app import create_app


def _holders(ref: weakref.ref) -> str:
    app = ref()
    if app is None:
        return ""
    return ", ".join(sorted({type(r).__qualname__ for r in gc.get_referrers(app)}))


def test_a_built_app_is_freed_once_dropped(tmp_path):
    ref = weakref.ref(create_app(make_test_server(tmp_path), with_scheduler=False))
    gc.collect()
    assert ref() is None, f"a dropped app is still held by: {_holders(ref)}"


def test_an_app_that_served_requests_is_freed_once_its_client_closes(tmp_path):
    """Serving is what classifies the callables a route reaches (and starts the slow-request
    watcher, which holds its app until it retires — made quick here, it is 2 s live)."""
    app = create_app(make_test_server(tmp_path), with_scheduler=False)
    watch = app.state.slow_watch
    watch.tick_s = 0.01
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        assert client.get("/api/status").status_code == 200
        assert client.post("/api/stream-ticket").status_code == 200
        assert client.get("/").status_code == 200
    thread = watch._thread
    if thread is not None:
        thread.join(timeout=5)
    ref = weakref.ref(app)
    del app, watch, thread, client
    gc.collect()
    assert ref() is None, f"an app that served requests is still held by: {_holders(ref)}"
