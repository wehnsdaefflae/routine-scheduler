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
            self._refresh_one(conn, prov)

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
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            self._mark_reauth(conn, "refresh returned no access_token")
            return
        conn.access_token = access
        if payload.get("refresh_token"):        # rotation: persist the NEW refresh token
            conn.refresh_token = payload["refresh_token"]
        expires_in = payload.get("expires_in")
        conn.expires_at = time.time() + float(expires_in) if expires_in else 0.0
        conn.needs_reauth = False
        store.set_connection(conn)
        log.info("oauth refresh: renewed %s", conn.key())

    def _mark_reauth(self, conn: Connection, why: str) -> None:
        conn.needs_reauth = True
        store.set_connection(conn)
        log.warning("oauth refresh: %s needs re-auth (%s)", conn.key(), why)
        notify.send(self.server,
                    f"OAuth connection {conn.key()} needs re-authorization ({why}). "
                    f"Reconnect it in Settings → Connections.",
                    title="Connection expired")
