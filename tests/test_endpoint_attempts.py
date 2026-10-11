"""The ATTEMPT seam: `with_retries` reports every retry boundary from INSIDE the retry loop.

F655 (operator-reported, 2026-10-10). A resumed run's first call went to claude-proxy, the proxy
never answered, the engine's 600 s read timeout cut it, and `with_retries` sent it again one
second later; the second attempt answered in 1m10s. For those 11 minutes `llm-tasks.jsonl` held
ONE `started` record and the console showed one call in flight — a hung run and a slow run were
indistinguishable from outside, and the operator had to read the proxy's container log to find
out which.

The structural cause is an ordering one: instrumentation wraps the adapter from OUTSIDE
(`endpoints/instrument.InstrumentedEndpoint.complete`) while the retry loop lives INSIDE it
(`anthropic_api:268`, `openai_compat:251/299` → `base.with_retries`). So the task record sees ONE
call however many attempts it took, worst case 3 × 600 s = 30 minutes behind a single `started`
row. The boundary therefore has to be reported from inside the loop, which is what this module
pins — and what the reporter is handed must be legible: an httpx timeout's `str()` is the EMPTY
STRING, so the error that ended his attempt rendered as `claude-proxy: ` with no cause in it.
"""

from __future__ import annotations

import httpx
import pytest

from rsched.endpoints import attempts
from rsched.endpoints.base import CONNECT_TIMEOUT, EndpointError, post_json, with_retries


def _flaky(n_failures: int, exc=None):
    """A call that fails `n_failures` times with a retryable error, then returns "ok"."""
    calls: list[int] = []

    def fn():
        calls.append(1)
        if len(calls) <= n_failures:
            raise exc or EndpointError("boom", retryable=True)
        return "ok"

    return fn, calls


def test_each_boundary_is_reported_from_inside_the_loop(monkeypatch):
    """One report per boundary, carrying the attempt that failed, the total allowed, what ended
    it and how long the loop will wait — the four facts the dock had no way to know."""
    monkeypatch.delenv("RSCHED_RETRY_BASE_DELAY", raising=False)  # pin the PRODUCTION schedule
    monkeypatch.setattr("time.sleep", lambda s: None)
    seen: list[tuple] = []

    fn, calls = _flaky(2)
    with attempts.reporting(lambda attempt, tries, error, retry_in:
                            seen.append((attempt, tries, str(error), retry_in))):
        assert with_retries(fn) == "ok"
    assert len(calls) == 3
    assert [(a, t) for a, t, _e, _s in seen] == [(1, 3), (2, 3)]
    assert all("boom" in e for _a, _t, e, _s in seen)
    assert [s for *_rest, s in seen] == [1.0, 2.0]          # the loop's real exponential waits


def test_an_exhausted_loop_reports_every_boundary_but_not_the_last_failure(monkeypatch):
    """The terminal failure is the wrapper's to record (it already does, as `failed`): the loop
    reports only the boundaries it is about to sleep through, so nothing is recorded twice."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    seen: list[int] = []
    fn, calls = _flaky(9)
    with attempts.reporting(lambda attempt, tries, error, retry_in: seen.append(attempt)), \
            pytest.raises(EndpointError, match="boom"):
        with_retries(fn)
    assert len(calls) == 3 and seen == [1, 2]


def test_a_non_retryable_error_reports_no_boundary(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    seen: list[int] = []
    with attempts.reporting(lambda attempt, tries, error, retry_in: seen.append(attempt)), \
            pytest.raises(EndpointError):
        with_retries(lambda: (_ for _ in ()).throw(EndpointError("fatal")))
    assert seen == []


def test_no_reporter_is_the_default_and_a_broken_one_never_breaks_the_call(monkeypatch):
    """Recording is strictly out-of-band, like every other part of this seam: with no reporter
    installed the loop behaves exactly as before, and a reporter that raises is swallowed."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    fn, calls = _flaky(1)
    assert with_retries(fn) == "ok" and len(calls) == 2      # nothing installed: unchanged

    fn2, calls2 = _flaky(1)
    with attempts.reporting(lambda *_a: (_ for _ in ()).throw(RuntimeError("reporter down"))):
        assert with_retries(fn2) == "ok"
    assert len(calls2) == 2


def test_the_reporter_does_not_leak_out_of_its_scope(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    seen: list[int] = []
    with attempts.reporting(lambda attempt, *_rest: seen.append(attempt)):
        pass
    fn, _calls = _flaky(1)
    assert with_retries(fn) == "ok"
    assert seen == [], "the reporter outlived its context"


# --- what ended the attempt has to be READABLE ------------------------------------------------
# The operator's own case: `httpx.ReadTimeout` carries an empty message, so post_json's
# f"{name}: {exc}" produced "claude-proxy: " — a retryable error whose text says nothing about
# the 600 s cut that caused it. The attempt record is only as useful as this string.

def test_a_read_timeout_names_the_bound_it_hit(monkeypatch):
    def boom(*_a, **_kw):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EndpointError) as ei:
        post_json("http://x/y", {}, None, 600, name="claude-proxy")
    msg = str(ei.value)
    assert ei.value.retryable
    assert "600" in msg and "read timeout" in msg.lower(), msg
    assert msg.strip() != "claude-proxy:", "the cause is still invisible"


def test_a_connect_timeout_names_the_connect_bound(monkeypatch):
    def boom(*_a, **_kw):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EndpointError) as ei:
        post_json("http://x/y", {}, None, 600, name="prov")
    msg = str(ei.value)
    assert f"{CONNECT_TIMEOUT:g}" in msg and "connect" in msg.lower(), msg
    assert "600" not in msg, f"the answer bound is not what a connect timeout hit: {msg}"


def test_an_ordinary_transport_error_keeps_its_own_words(monkeypatch):
    def boom(*_a, **_kw):
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EndpointError, match="All connection attempts failed"):
        post_json("http://x/y", {}, None, 600, name="prov")
