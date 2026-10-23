"""Is the credential behind each model endpoint still alive? The scheduled caller the probes
never had.

The daemon has kept user CONNECTIONS alive since `daemon/oauth_refresh.py`. The MODEL
credentials every run depends on had no such pass: on 2026-09-14 06:18 a claude-proxy
subscription token expired and eight routines failed at turn 0 within two minutes, each `503
auth_unavailable`. The probes to notice that existed already — `web/settings/endpoint_probe.py`
says storing endpoint config and "going out to the network to see whether it works are different
jobs", which is right; the second job simply had no scheduled caller. This is it.

What it probes with, and why that one: `endpoints/cliproxy_login.accounts` — the proxy's own
account listing, which carries each account's status word and the proxy's one-line reason when
one is unavailable ("a dead refresh token", a usage limit). Three reasons it is the probe for
this pass:

- it is the LIGHTEST of the three already-implemented probes. The credits read and the quota
  read each make an upstream request (api.anthropic.com through the proxy, or a provider's
  balance API); this is one GET against the proxy on the same host. The live completion test is
  never used here at any cadence — it costs a completion, which is what a watchdog may not do;
- it answers the INCIDENT's question directly. A dead subscription credential is exactly what
  the proxy reports as an unavailable account, and it says so before any run asks for a
  completion;
- it needs nothing moved. It already lives outside the settings router, so this manager calls
  the same function the Settings card does, with no extraction and no second copy of the logic.

WHAT IS NOT COVERED, stated plainly: an endpoint that resolves no proxy management binding is
skipped and gets no record at all. For an API-key endpoint with a balance API (OpenRouter,
Nano-GPT) the light probe is the CREDITS read, and that one is still inline in
`web/settings/endpoint_probe.py` — extracting it is its own change. Until then this manager
claims nothing about those endpoints, rather than claiming they are healthy.

CADENCE. Every 10 minutes (`CHECK_EVERY_S`), never the 5-second tick, and one pass at a time
(`Scheduler._off_tick`): a credential dies on the order of a token lifetime, and this is a
watchdog, not a monitor. The pass runs in a worker thread so a proxy that answers slowly cannot
hold a fire behind it — the lesson `_off_tick`'s docstring records.

The event is filed at most once a day while the condition holds (`REFILE_AFTER`), the
`daemon/backup_watch` rule for a standing condition, with one difference: the latch lives in the
RECORD (`endpoint_health`'s `filed_at`) rather than on this object. A process-memory latch
re-files the whole fleet's standing conditions at every daemon restart, and a restart is common
in exactly the window an operator is fixing a credential.
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

#: Which probe wrote a record — carried on the row and the event, so a reader of either knows
#: what was actually asked (and a later increment's heavier probe is distinguishable).
PROBE = "cliproxy-accounts"

EVENT = "endpoint_needs_reauth"


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
        for name in sorted(self.server.endpoints or {}):
            try:
                self._probe_one(name)
            except Exception:   # one bad endpoint must not starve the rest of the pass
                log.exception("endpoint watch: probing %s failed", name)

    def _probe_one(self, name: str) -> None:
        bound = cliproxy_mgmt.binding(self.server, name)
        if bound is None:
            return      # no light probe reaches this endpoint — see the module docstring
        reply = cliproxy_login.accounts(
            bound, cliproxy_mgmt.endpoint_providers(self.server, name))
        state, detail = endpoint_health.verdict(reply)
        previous = endpoint_health.get(self.server.routines_home, name) or {}
        # A state CHANGE is a new fact and is reported; the same state re-reports once a day.
        filed_at = str(previous.get("filed_at") or "") if previous.get("state") == state else ""
        if state == "needs_reauth" and _due(filed_at):
            log_health_event(self.server.routines_home, EVENT, routine="", run_id="",
                             detail=detail, endpoint=name, probe=PROBE)
            log.warning("endpoint watch: %s needs re-auth (%s)", name, detail)
            filed_at = now_iso()
        endpoint_health.remember(self.server.routines_home, name, state=state, detail=detail,
                                 probe=PROBE, filed_at=filed_at)


def _due(filed_at: str) -> bool:
    """Has this state gone unreported for long enough to say again? An unstamped or unreadable
    stamp means YES — a latch that cannot read its own history must not become the thing that
    silences the log (`health_cooldown.blocks` fails open for the same reason).
    """
    if not filed_at:
        return True
    when = instant(filed_at)
    return when is None or datetime.now(UTC) - when >= REFILE_AFTER
