"""The daemon asks whether each subscription LOGIN behind the gateway is still alive, and says
so once a day (C5: re-pointed from endpoints to the gateway's `plans:` logins)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from helpers import health_events, mock_proxy, server_config
from rsched.config import EndpointConfig, GatewayConfig
from rsched.daemon import endpoint_watch
from rsched.daemon.endpoint_watch import EndpointWatch
from rsched.endpoints import cliproxy_mgmt, endpoint_health

LOGIN = "otari/claude"


def _server(tmp_path, plans=None, mgmt=("cliproxy-mgmt",)):
    """This instance's shape: models reach the Otari gateway, whose `plans:` name the CLIProxyAPI
    logins, and ONE model-less endpoint carries the proxy's management binding."""
    endpoints = {"otari": EndpointConfig(name="otari", kind="anthropic", gateway="otari",
                                         base_url="http://gw:4000/api")}
    for name in mgmt:
        endpoints[name] = EndpointConfig(name=name, kind="anthropic", base_url="http://proxy:8318",
                                         quota_source="cliproxy")
    server = server_config(
        routines_home=tmp_path / "routines", endpoints=endpoints,
        gateways={"otari": GatewayConfig(name="otari",
                                         plans=plans or {"cliproxy": ["claude"]})})
    server.routines_home.mkdir(parents=True, exist_ok=True)
    return server


def _files(*rows: dict):
    """An auth-file listing handler, the one management route this probe may call."""
    def handler(request):
        assert request.url.path == "/v0/management/auth-files", (
            f"the light probe may only read the proxy's account list, not {request.url.path} — "
            "a credits/quota read goes upstream and the completion test costs a completion")
        return httpx.Response(200, json={"files": list(rows)})
    return handler


def _claude(**over) -> dict:
    return {"name": "claude-me.json", "provider": "claude", "email": "me@example.org",
            "status": "active", **over}


@pytest.fixture(autouse=True)
def _mgmt_key(monkeypatch):
    """The proxy's management key, resolved on the server alone (cliproxy_mgmt). AUTOUSE, not a
    parameter: ruff's PT019 refuses an underscore-named fixture argument and vulture reads a
    plainly-named one as an unused variable, so a test that merely needs the key declares nothing.
    """
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "management-secret")


# ---- the verdict: what an account listing MEANS -------------------------------------------


def test_a_dead_credential_is_needs_reauth_and_a_usage_limit_is_only_cooling():
    """The trap `oauth_refresh` already avoids, in the proxy's dialect: it flags `needs_reauth`
    only on a REFUSAL (400/401/403) because a flagged record is withheld from every run, so a
    429 must not cost a re-authorization by hand. CLIProxyAPI reports a dead refresh token and a
    usage limit with the SAME `unavailable` flag, and only the second carries a retry time."""
    dead = {"ok": True, "accounts": [{"label": "me@example.org", "unavailable": True,
                                      "status_message": "refresh token expired"}]}
    state, detail = endpoint_health.verdict(dead)
    assert state == "needs_reauth"
    assert "me@example.org" in detail and "refresh token expired" in detail
    assert "turn 0" in detail          # the reader has to learn what it costs

    limited = {"ok": True, "accounts": [{"label": "me@example.org", "unavailable": True,
                                         "status_message": "usage_limit_reached",
                                         "next_retry_after": "2026-09-20T06:00:16+02:00"}]}
    assert endpoint_health.verdict(limited)[0] == "cooling"


def test_one_live_account_is_ok_even_beside_a_dead_one():
    """The login still serves: the fold must not read as blocked while a credential works."""
    reply = {"ok": True, "accounts": [{"label": "dead@example.org", "unavailable": True},
                                      {"label": "live@example.org"}]}
    state, detail = endpoint_health.verdict(reply)
    assert state == "ok" and "live@example.org" in detail


@pytest.mark.parametrize("reply", [
    {"supported": True, "ok": False, "error": "Could not reach the proxy's management API"},
    {"ok": True, "accounts": []},
    {"ok": True, "accounts": [{"label": "off@example.org", "disabled": True}]},
])
def test_nothing_is_claimed_when_nothing_was_established(reply):
    """`backup_watch`'s rule: an unreadable source reports UNKNOWN, never a failure. Claiming a
    credential is dead because the proxy was unreachable — or because a fresh install has signed
    no account in — is the invention F480 removed from the boot reap."""
    assert endpoint_health.verdict(reply)[0] == "unknown"


@pytest.mark.parametrize("reply", [
    {"ok": False, "error": "unreachable"},
    {"ok": True, "accounts": [{"label": "live@example.org"}]},
    {"ok": True, "accounts": [{"label": "x", "unavailable": True}]},
    {"ok": True, "accounts": [{"label": "x", "unavailable": True, "next_retry_after": "x"}]},
])
def test_every_verdict_is_one_of_the_declared_states_with_a_detail(reply):
    """`STATES` is the vocabulary a reader of the record keys on, so a state outside it is a
    record nothing can read — the same failure mode the health enum is machine-checked for. The
    detail is never empty: a flag without a reason sends the operator to the daemon log."""
    state, detail = endpoint_health.verdict(reply)
    assert state in endpoint_health.STATES
    assert detail.strip()


# ---- the pass: the record, the event, and what it may ask -------------------------------


async def test_a_dead_proxy_credential_lands_in_the_record_and_the_health_stream(tmp_path,
                                                                                 monkeypatch):
    """The whole point of the item: before this, a dead model credential was visible nowhere
    until a run spent its first turn on it."""
    server = _server(tmp_path)
    mock_proxy(monkeypatch, _files(_claude(status="error", unavailable=True,
                                           status_message="refresh token expired")))

    await EndpointWatch(server).tick()

    row = endpoint_health.get(server.routines_home, LOGIN)
    assert row is not None
    assert row["state"] == "needs_reauth" and row["needs_reauth"] is True
    assert row["probe"] == endpoint_watch.PROBE and row["checked_at"]

    rows = health_events(server.routines_home, event="endpoint_needs_reauth")
    assert len(rows) == 1
    assert rows[0]["login"] == LOGIN and rows[0]["probe"] == endpoint_watch.PROBE
    assert rows[0]["gateway"] == "otari" and rows[0]["provider"] == "claude"
    # an instance fact, not a routine's — `backup_stale`'s idiom
    assert rows[0]["routine"] == "" and rows[0]["run_id"] == ""
    assert "refresh token expired" in rows[0]["detail"]


async def test_a_healthy_credential_is_recorded_and_files_nothing(tmp_path, monkeypatch):
    server = _server(tmp_path)
    mock_proxy(monkeypatch, _files(_claude()))

    await EndpointWatch(server).tick()

    assert endpoint_health.get(server.routines_home, LOGIN)["state"] == "ok"
    assert health_events(server.routines_home, event="endpoint_needs_reauth") == []


async def test_a_gateway_with_no_plans_names_no_login_and_asks_nothing(tmp_path, monkeypatch):
    """A gateway provider without `plans` is a metered key (OpenRouter, Nano-GPT), not a login:
    no record, no event, and no request either."""
    server = server_config(routines_home=tmp_path / "routines",
                           endpoints={"or": EndpointConfig(name="or", kind="openai",
                                                           base_url="https://openrouter.ai/api/v1")},
                           gateways={"otari": GatewayConfig(name="otari")})
    server.routines_home.mkdir(parents=True, exist_ok=True)
    mock_proxy(monkeypatch, lambda request: pytest.fail("no request expected"))

    await EndpointWatch(server).tick()

    assert endpoint_health.load(server.routines_home) == {}


async def test_each_login_is_judged_alone_so_one_dead_subscription_is_not_hidden(tmp_path,
                                                                                monkeypatch):
    """The point of C5: the per-endpoint probe judged every account in ONE list, so a dead Codex
    login beside a live Claude one read `ok` ("2 of 3 available"). Per login, it is flagged —
    and the whole pass costs ONE read of the proxy's account list."""
    server = _server(tmp_path, plans={"cliproxy": ["claude", "antigravity"],
                                      "chatgpt": ["codex-plus"]})
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return _files(_claude(), {"name": "ag.json", "provider": "antigravity",
                                  "email": "me@example.org", "status": "active"},
                      {"name": "codex-plus.json", "provider": "codex", "email": "me@example.org",
                       "unavailable": True, "status_message": "refresh token revoked"})(request)

    mock_proxy(monkeypatch, handler)
    await EndpointWatch(server).tick()

    states = {k: v["state"] for k, v in endpoint_health.load(server.routines_home).items()}
    assert states == {"otari/claude": "ok", "otari/antigravity": "ok",
                      "otari/codex": "needs_reauth"}
    assert len(calls) == 1
    [row] = health_events(server.routines_home, event="endpoint_needs_reauth")
    assert row["login"] == "otari/codex" and "refresh token revoked" in row["detail"]


@pytest.mark.parametrize("mgmt", [(), ("mgmt-a", "mgmt-b")])
async def test_which_proxy_holds_the_logins_is_never_guessed(tmp_path, monkeypatch, mgmt):
    """No management binding, or two: every login is UNKNOWN with the reason — never `ok`, never
    a request to a proxy picked by order."""
    server = _server(tmp_path, mgmt=mgmt)
    mock_proxy(monkeypatch, lambda request: pytest.fail("no request expected"))

    await EndpointWatch(server).tick()

    row = endpoint_health.get(server.routines_home, LOGIN)
    assert row["state"] == "unknown" and "management binding" in row["detail"]


async def test_an_unknown_plan_group_is_unknown_and_the_rest_are_still_judged(tmp_path,
                                                                              monkeypatch):
    server = _server(tmp_path, plans={"cliproxy": ["claude"], "mystery": ["kiro-pro"]})
    mock_proxy(monkeypatch, _files(_claude()))

    await EndpointWatch(server).tick()

    rows = endpoint_health.load(server.routines_home)
    assert rows[LOGIN]["state"] == "ok"
    assert rows["otari/kiro-pro"]["state"] == "unknown"
    assert "names no login provider" in rows["otari/kiro-pro"]["detail"]


async def test_a_login_no_gateway_names_any_more_leaves_the_record(tmp_path, monkeypatch):
    """The per-endpoint keys of the probe this replaces (`claude-proxy`, `cliproxy-mgmt`) would
    otherwise sit in the record forever, and the blocked-work surface reads that record."""
    server = _server(tmp_path)
    endpoint_health.remember(server.routines_home, "claude-proxy", state="needs_reauth",
                             detail="old", probe=endpoint_watch.PROBE)
    mock_proxy(monkeypatch, _files(_claude()))

    await EndpointWatch(server).tick()

    assert set(endpoint_health.load(server.routines_home)) == {LOGIN}


# ---- the cadence: minutes, and one row a day ---------------------------------------------


async def test_the_probe_runs_on_its_own_slow_interval_not_the_five_second_tick(tmp_path,
                                                                               monkeypatch):
    """A credential dies on the order of a token lifetime. The scheduler ticks every 5 s, and a
    pass per tick would be ~17k management calls a day — the cost `library_watch.CHECK_EVERY_S`
    exists to avoid, on the same loop executor."""
    assert endpoint_watch.CHECK_EVERY_S >= 60
    server = _server(tmp_path)
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"files": [_claude()]})

    mock_proxy(monkeypatch, handler)
    watch = EndpointWatch(server)
    await watch.tick()
    await watch.tick()                 # the very next tick: still inside the interval
    assert len(calls) == 1


async def test_a_standing_dead_credential_files_one_row_a_day_across_a_daemon_restart(
        tmp_path, monkeypatch):
    """`backup_watch`'s one-row-a-day rule, with its latch moved into the RECORD. A latch held on
    the manager object re-files the whole fleet's standing conditions at every daemon restart —
    and a restart is common in exactly the window an operator is fixing a credential."""
    server = _server(tmp_path)
    mock_proxy(monkeypatch, _files(_claude(unavailable=True, status_message="token expired")))

    await EndpointWatch(server).tick()
    await EndpointWatch(server).tick()        # a FRESH manager: the daemon restarted
    assert len(health_events(server.routines_home, event="endpoint_needs_reauth")) == 1

    # a day later the condition is still true, and still worth saying
    stale = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    endpoint_health.remember(server.routines_home, LOGIN, state="needs_reauth",
                             detail="token expired", probe=endpoint_watch.PROBE,
                             filed_at=stale)
    await EndpointWatch(server).tick()
    assert len(health_events(server.routines_home, event="endpoint_needs_reauth")) == 2


async def test_a_credential_that_comes_back_reports_its_next_death(tmp_path, monkeypatch):
    """A state that CHANGES clears the latch, so the next death is reported rather than
    swallowed by yesterday's row — `backup_watch`'s "a stamp that becomes fresh again clears
    the memory"."""
    server = _server(tmp_path)
    dead = _claude(unavailable=True, status_message="token expired")
    # ONE patch, re-aimed between passes: `helpers.mock_proxy` wraps whatever `httpx.Client` is
    # at call time, so patching twice passes `transport=` to an already-wrapped factory and the
    # TypeError reads as an unreachable proxy.
    current = [dead]
    mock_proxy(monkeypatch, lambda request: httpx.Response(200, json={"files": current}))

    await EndpointWatch(server).tick()
    assert endpoint_health.get(server.routines_home, LOGIN)["state"] == "needs_reauth"

    current[:] = [_claude()]
    await EndpointWatch(server).tick()
    assert endpoint_health.get(server.routines_home, LOGIN)["state"] == "ok"

    current[:] = [dead]
    await EndpointWatch(server).tick()
    assert len(health_events(server.routines_home, event="endpoint_needs_reauth")) == 2


# ---- the registration in the daemon ----------------------------------------------------


async def test_a_running_scheduler_ticks_the_endpoint_watch_beside_its_tick(tmp_path,
                                                                           monkeypatch):
    """Registration in the daemon, proved by running one: a manager nothing ticks is a probe
    that never fires, which is exactly the state the three probes were already in. Awaited IN
    the tick a slow proxy would hold every fire behind it, so it goes through `_off_tick` —
    whose task dict is asserted here beside the call (`Scheduler._off_tick`'s own lesson, after
    OAuth and the library watch).
    """
    import rsched.daemon.scheduler as sched_mod
    from conftest import FakeRunner
    from rsched.daemon.events import EventBus
    from rsched.daemon.scheduler import Scheduler

    ticked = asyncio.Event()

    async def fake_tick(self) -> None:
        ticked.set()

    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    monkeypatch.setattr(EndpointWatch, "tick", fake_tick)
    server = _server(tmp_path)
    sched = Scheduler(server, FakeRunner(), EventBus())
    assert isinstance(sched.credentials, EndpointWatch)

    task = asyncio.create_task(sched.run_forever())
    try:
        await asyncio.wait_for(ticked.wait(), 5)
    finally:
        task.cancel()
    assert "endpoint-watch" in sched._off_tick_tasks
