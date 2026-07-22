"""Refresh expiring OAuth access tokens on the scheduler tick.

The daemon/web process is the single writer of the connection store; this manager keeps each
EXPIRING-provider connection's access token valid (refreshing ~5 min before expiry) and persists
any ROTATED refresh_token, so a run always reads a live token from disk. Non-expiring providers
(Notion — long-lived bearer, no refresh_token) are skipped, so an instance that only uses those
never does any work here. A refresh that the provider rejects flags the connection `needs_reauth`
and pings the user through the one notification seam.

The token exchange is blocking httpx (mirrors the connect flow); the async `tick` runs it in a
worker thread so it never blocks the scheduler loop. Store writes are serialized by the store's
own lock, so the worker thread and the web callback can't clobber each other.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .. import notify
from ..config import ServerConfig
from ..oauth import providers, store
from ..oauth.providers import Provider
from ..oauth.store import Connection

log = logging.getLogger("rsched.oauth.refresh")

REFRESH_MARGIN_S = 300   # refresh once a token is within 5 minutes of expiry
# A refresh response that reports no expires_in gets this assumed lifetime — writing
# expires_at=0 instead made "unknown" look permanently due and hammered the provider
# every 5-second tick.
DEFAULT_TOKEN_LIFETIME_S = 3600.0


class OAuthRefreshManager:
    """Ticked from the scheduler loop like the trigger/detached managers."""

    def __init__(self, server: ServerConfig):
        self.server = server

    async def tick(self) -> None:
        """One refresh pass, off the event loop. Never raises into the scheduler."""
        try:
            await asyncio.to_thread(self._refresh_due, time.time())
        except Exception:
            log.exception("oauth refresh tick failed")

    def _refresh_due(self, now: float) -> None:
        for conn in store.load_connections().values():
            prov = providers.get_provider(conn.provider)
            if prov is None or not prov.expiring or not conn.refresh_token or conn.needs_reauth:
                continue
            # expires_at == 0 means "unknown/never" — refresh it too, defensively
            if conn.expires_at and conn.expires_at - now > REFRESH_MARGIN_S:
                continue
            try:
                self._refresh_one(conn, prov)
            except Exception:   # one bad connection must not starve the rest of the pass
                log.exception("oauth refresh: %s failed", conn.key())

    def _refresh_one(self, conn: Connection, prov: Provider) -> None:
        creds = providers.client_creds(conn.provider)
        if creds is None:
            log.warning("oauth refresh: no client creds for %s — skipping", conn.provider)
            return
        data = {"grant_type": "refresh_token", "refresh_token": conn.refresh_token}
        auth: tuple[str, str] | None = None
        if prov.exchange_auth == "basic":
            auth = (creds.client_id, creds.client_secret)
        else:
            data["client_id"] = creds.client_id
            if creds.client_secret:
                data["client_secret"] = creds.client_secret
        try:
            if prov.exchange_encoding == "json":
                resp = httpx.post(prov.token_url, json=data, auth=auth,
                                  headers={"Accept": "application/json"}, timeout=20)
            else:
                resp = httpx.post(prov.token_url, data=data, auth=auth,
                                  headers={"Accept": "application/json"}, timeout=20)
        except httpx.HTTPError as exc:
            log.warning("oauth refresh: network error for %s: %s", conn.key(), exc)
            return   # transient — try again next tick, don't flag needs_reauth
        if resp.status_code != 200:
            self._mark_reauth(conn, f"refresh HTTP {resp.status_code}")
            return
        try:
            payload = resp.json()
        except ValueError:
            # a 200 with a garbled body is a provider anomaly, not a dead grant —
            # transient like a network error; try again next tick
            log.warning("oauth refresh: %s returned unparseable JSON — retrying later",
                        conn.key())
            return
        access = payload.get("access_token")
        if not access:
            self._mark_reauth(conn, "refresh returned no access_token")
            return
        expires_in = payload.get("expires_in")
        old_refresh = conn.refresh_token

        def apply(cur: Connection | None) -> Connection | None:
            # Compare-and-swap on the refresh token: if the record changed while our
            # exchange was in flight (the user re-authorized in Settings), the CURRENT
            # record is fresher — writing our result would clobber the new grant.
            if cur is None or cur.refresh_token != old_refresh:
                return None
            cur.access_token = access
            if payload.get("refresh_token"):    # rotation: persist the NEW refresh token
                cur.refresh_token = payload["refresh_token"]
            cur.expires_at = time.time() + (float(expires_in) if expires_in
                                            else DEFAULT_TOKEN_LIFETIME_S)
            cur.needs_reauth = False
            return cur

        if store.update_connection(conn.provider, conn.account, apply):
            log.info("oauth refresh: renewed %s", conn.key())
        else:
            log.info("oauth refresh: %s changed during the exchange — kept the newer record",
                     conn.key())

    def _mark_reauth(self, conn: Connection, why: str) -> None:
        def apply(cur: Connection | None) -> Connection | None:
            if cur is None or cur.refresh_token != conn.refresh_token:
                return None   # re-authorized meanwhile — nothing to flag
            cur.needs_reauth = True
            return cur

        if not store.update_connection(conn.provider, conn.account, apply):
            return
        log.warning("oauth refresh: %s needs re-auth (%s)", conn.key(), why)
        # Discord is OPT-IN (the communication permission). An instance-level event pings
        # only when a routine/conversation actually BINDS this connection and holds the
        # permission — the web record (Settings badge) is the always-on surface.
        if self._discord_opted_in(conn):
            notify.send(self.server,
                        f"OAuth connection {conn.key()} needs re-authorization ({why}). "
                        f"Reconnect it in Settings → Connections.",
                        title="Connection expired")

    def _discord_opted_in(self, conn: Connection) -> bool:
        from . import registry

        for home in (self.server.routines_home, self.server.conversations_home):
            for info in registry.scan(self.server, home).values():
                if (info.cfg.connections.get(conn.provider) == conn.account
                        and notify.discord_enabled(self.server,
                                                   permissions=info.cfg.permissions)):
                    return True
        return False
