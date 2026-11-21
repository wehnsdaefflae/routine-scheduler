"""A gateway that REFUSES the connection is waited out, not failed over (2026-11-19)."""

import pytest

from rsched.endpoints.base import EndpointError, with_retries


def test_a_refused_connection_is_waited_out_not_failed_over(monkeypatch):
    """2026-11-19: the model gateway restarted for 14 s; three tries in ~3 s failed both rungs of
    Opus's chain (every rung is the same gateway) and ended llmsectest-weekday's run. A refused
    connection — nothing was received — is retried for UNREACHABLE_PATIENCE instead, each wait
    capped at UNREACHABLE_STEP; any other retryable error keeps its three tries."""
    import httpx

    from rsched.endpoints import base

    monkeypatch.delenv("RSCHED_RETRY_BASE_DELAY", raising=False)  # production units
    clock = [0.0]
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    calls = []

    def refused(*_a, **_k):
        calls.append(1)
        if clock[0] < 14:                 # the gateway is down for its restart
            raise httpx.ConnectError("[Errno 111] Connection refused")
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "post", refused)
    resp = with_retries(lambda: base.post_json("http://gw", {}, None, 5, name="otari"))
    assert resp.status_code == 200 and len(calls) > 3
    # a gateway that never comes back is given up on after the patience window
    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: (_ for _ in ()).throw(
        httpx.ConnectError("refused")))
    clock[0] = 0.0
    with pytest.raises(EndpointError) as ei:
        with_retries(lambda: base.post_json("http://gw", {}, None, 5, name="otari"))
    assert ei.value.unreachable and base.UNREACHABLE_PATIENCE <= clock[0] \
        <= base.UNREACHABLE_PATIENCE + base.UNREACHABLE_STEP
