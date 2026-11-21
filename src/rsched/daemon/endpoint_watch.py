"""Is each SUBSCRIPTION LOGIN behind the model gateway still alive? The scheduled caller the probes
never had.

The daemon has kept user CONNECTIONS alive since `daemon/oauth_refresh.py`. The MODEL
credentials every run depends on had no such pass: on 2026-09-14 06:18 a claude-proxy
subscription token expired and eight routines failed at turn 0 within two minutes, each `503
auth_unavailable`. This is that pass.

WHAT IT WATCHES — the logins, not the endpoints (C5, operator 2026-11-21). Every model call
leaves through the Otari gateway (docs/model-gateway.md), so no endpoint in `config.yaml` is a
proxy any more: `otari`, `otari-openai` and `otari-openai-plain` all reach Otari, and Otari
routes a name to the standalone CLIProxyAPI that holds the subscription logins. The facts that
say which logins those are live in the gateway's config, never on the wire:
`gateways.<name>.plans` maps each gateway provider that is a CLIProxyAPI login to the catalog
plan groups it signs in with (`cliproxy: [claude, antigravity]`, `chatgpt: [codex-plus]`), and
each plan group belongs to one auth-file provider of the proxy (`cliproxy_mgmt.plan_provider`:
`codex-plus` is a `codex` login). So the watch judges ONE record per (gateway, login provider) —
`otari/claude`, `otari/antigravity`, `otari/codex` — over the accounts of that provider alone.
The per-endpoint probe it replaces judged all three logins in one list, where "2 of 3 available"
read `ok` while one subscription was dead.

What it probes with: `endpoints/cliproxy_login.accounts`, the proxy's own account listing — one
READ-ONLY GET against the management API of the instance's one `quota_source: cliproxy` endpoint
(`cliproxy-mgmt`), fetched ONCE per pass and filtered per login. It carries each account's status
word and the proxy's one-line reason. The credits read, the quota read and the live completion
test each go upstream; none is used here.

WHAT IS NOT COVERED, stated plainly: a gateway provider with no `plans` (OpenRouter, Nano-GPT,
Anthropic's API) is a metered key, not a login, and gets no record. When the instance has no
management endpoint, or more than one, every login is recorded `unknown` with that reason —
which proxy holds the logins is not something to guess.

CADENCE. Every 10 minutes (`CHECK_EVERY_S`), never the 5-second tick, and one pass at a time
(`Scheduler._off_tick`): a credential dies on the order of a token lifetime. The pass runs in a
worker thread so a proxy that answers slowly cannot hold a fire behind it.

The event is filed at most once a day while the condition holds (`REFILE_AFTER`), with the latch
in the RECORD (`endpoint_health`'s `filed_at`) so a daemon restart cannot re-file it. A record
for a login no gateway names any more is dropped at the end of the pass, so the blocked-work
surface (`web/operator_conditions`) never asks a retired login whether it is still dead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from ..config import ServerConfig
from ..endpoints import cliproxy_login, cliproxy_mgmt, endpoint_health
from ..health_events import log_health_event
from ..ids import now_iso
from ..readmodels.stamps import instant

log = logging.getLogger("rsched.endpoint_watch")

#: How often the credentials are actually asked about. Minutes, deliberately: see the docstring.
CHECK_EVERY_S = 600.0

#: How long one filing keeps the next quiet while the same state holds.
REFILE_AFTER = timedelta(days=1)

#: Which probe wrote a record — carried on the row and the event.
PROBE = "cliproxy-accounts"

EVENT = "endpoint_needs_reauth"


def logins(server: ServerConfig) -> list[tuple[str, str, str]]:
    """Every subscription login a gateway names, as (record key, gateway, login provider).

    A plan group no login provider claims is kept with provider "" — it is judged `unknown`
    with that reason rather than dropped, since dropping it would claim nothing was configured.
    """
    out: dict[str, tuple[str, str, str]] = {}
    for gname, gateway in sorted((server.gateways or {}).items()):
        for plans in (gateway.plans or {}).values():
            for plan in plans:
                provider = cliproxy_mgmt.plan_provider(plan)
                key = f"{gname}/{provider or plan}"
                out.setdefault(key, (key, gname, provider))
    return list(out.values())


class EndpointWatch:
    """The daemon's credential check: one pass every CHECK_EVERY_S, run BESIDE the tick."""

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
        self._last_check: float | None = None   # monotonic stamp of the last pass

    async def tick(self) -> None:
        """One probe pass, off the event loop and behind its own interval. Never raises into
        the scheduler: a check about a credential must not be the thing that stops the daemon.
        """
        now = time.monotonic()
        if self._last_check is not None and now - self._last_check < CHECK_EVERY_S:
            return
        self._last_check = now
        try:
            await asyncio.to_thread(self._probe_all)
        except Exception:
            log.exception("endpoint watch tick failed")

    def _probe_all(self) -> None:
        targets = logins(self.server)
        home = self.server.routines_home
        if not targets:
            endpoint_health.keep_only(home, set())
            return
        mgmt, why = cliproxy_mgmt.management_endpoint(self.server)
        listing = (cliproxy_login.accounts(mgmt) if mgmt is not None
                   else {"ok": False, "error": why})
        for key, gateway, provider in targets:
            try:
                self._judge(key, gateway, provider, listing)
            except Exception:   # one bad login must not starve the rest of the pass
                log.exception("endpoint watch: judging %s failed", key)
        endpoint_health.keep_only(home, {key for key, _, _ in targets})

    def _judge(self, key: str, gateway: str, provider: str, listing: dict) -> None:
        if not provider:
            state, detail = "unknown", (f"plan group {key.split('/', 1)[1]!r} of gateway "
                                        f"{gateway!r} names no login provider this instance "
                                        f"knows ({', '.join(cliproxy_mgmt.LOGIN_PROVIDER)}) — "
                                        "nothing is claimed about it")
        else:
            rows = [r for r in (listing.get("accounts") or []) if r.get("provider") == provider]
            state, detail = endpoint_health.verdict({**listing, "accounts": rows})
        home = self.server.routines_home
        previous = endpoint_health.get(home, key) or {}
        # A state CHANGE is a new fact and is reported; the same state re-reports once a day.
        filed_at = str(previous.get("filed_at") or "") if previous.get("state") == state else ""
        if state == "needs_reauth" and _due(filed_at):
            log_health_event(home, EVENT, routine="", run_id="", detail=f"{key}: {detail}",
                             login=key, gateway=gateway, provider=provider, probe=PROBE)
            log.warning("endpoint watch: %s needs re-auth (%s)", key, detail)
            filed_at = now_iso()
        endpoint_health.remember(home, key, state=state, detail=detail, probe=PROBE,
                                 filed_at=filed_at)


def _due(filed_at: str) -> bool:
    """Has this state gone unreported for long enough to say again? An unstamped or unreadable
    stamp means YES — a latch that cannot read its own history must not become the thing that
    silences the log (`health_cooldown.blocks` fails open for the same reason).
    """
    if not filed_at:
        return True
    when = instant(filed_at)
    return when is None or datetime.now(UTC) - when >= REFILE_AFTER
