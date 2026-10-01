"""Read account quota through CLIProxyAPI without reading its OAuth credentials.

The management key is resolved only on the server. The upstream URL is fixed; this
adapter never exposes the management API's general-purpose outbound-call facility.
"""

from __future__ import annotations

import json

import httpx

from ..config import EndpointConfig
from .anthropic_api import API_VERSION
from .base import EndpointError
from .cliproxy_mgmt import auth_files, file_provider
from .cliproxy_mgmt import client as _client
from .subscription_quota import normalize

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
MANAGE_URL = "https://claude.ai/settings/usage"


def _account(files: list[dict], selected: str) -> str:
    accounts = [row for row in files if file_provider(row) == "claude"
                and not row.get("disabled") and row.get("auth_index")]
    if selected:
        accounts = [row for row in accounts if str(row["auth_index"]) == selected]
    if len(accounts) != 1:
        raise EndpointError("Select one enabled Claude account in the proxy and set its "
                            "quota account index in Settings (blank works for one account).")
    return str(accounts[0]["auth_index"])


def read_quota(cfg: EndpointConfig, *, timeout: int = 15) -> dict:
    """Return real account windows or a soft, credential-free error for the console."""
    base = {"supported": True, "manage_url": MANAGE_URL}
    try:
        client, url = _client(cfg, timeout)
        with client:
            auth_index = _account(auth_files(client, url), cfg.quota_auth_index)
            response = client.post(f"{url}/api-call", json={
                "auth_index": auth_index, "method": "GET", "url": ENDPOINT,
                "header": {"Authorization": "Bearer $TOKEN$",
                           "anthropic-version": API_VERSION,
                           "anthropic-beta": "oauth-2025-04-20"},
            })
            if response.status_code != 200:
                raise EndpointError(f"Proxy quota request HTTP {response.status_code}.")
            envelope = response.json()
            if not isinstance(envelope, dict):
                raise EndpointError("Proxy quota response has an unrecognised format.")
            status = envelope.get("status_code")
            if status != 200:
                raise EndpointError(f"Claude usage HTTP {status}; reconnect the account through "
                                    "the proxy with profile access (not a setup-token).")
            raw = envelope.get("body")
            if isinstance(raw, str):
                raw = json.loads(raw)
            windows = normalize(raw if isinstance(raw, dict) else {})
            if not windows:
                raise EndpointError("Claude returned no recognisable quota windows.")
            return {**base, "ok": True, "windows": windows, "auth_index": auth_index}
    except EndpointError as exc:
        return {**base, "ok": False, "error": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError, OverflowError):
        # No upstream response bodies or exception URLs: those may contain credentials.
        return {**base, "ok": False,
                "error": "Could not read proxy quota; check connectivity and proxy health."}
