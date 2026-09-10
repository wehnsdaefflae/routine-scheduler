"""Read account quota through CLIProxyAPI without reading its OAuth credentials.

The management key is resolved only on the server. The upstream URL is fixed; this
adapter never exposes the management API's general-purpose outbound-call facility.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from ..config import EndpointConfig
from .base import EndpointError, resolve_api_key
from .subscription_quota import normalize

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
MANAGE_URL = "https://claude.ai/settings/usage"


def _account(files: list, selected: str) -> str:
    accounts = [row for row in files if isinstance(row, dict)
                and row.get("provider", row.get("type")) == "claude"
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
        origin = urlsplit(cfg.base_url)
        if origin.scheme not in {"http", "https"} or not origin.netloc or origin.username:
            raise EndpointError("Set the proxy's HTTP base URL in Settings.")
        key = resolve_api_key(name="CLIProxyAPI management", api_key="",
                              key_var=cfg.quota_key_var, key_env_file="", required=True)
        url = cfg.base_url.rstrip("/").removesuffix("/v1") + "/v0/management"
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          headers={"Authorization": f"Bearer {key}"}) as client:
            response = client.get(f"{url}/auth-files")
            if response.status_code != 200:
                raise EndpointError(f"Proxy management HTTP {response.status_code}; check the "
                                    "management key and management access configuration.")
            listing = response.json()
            files = listing.get("files") if isinstance(listing, dict) else None
            if not isinstance(files, list):
                raise EndpointError("Proxy account listing has an unrecognised format.")
            auth_index = _account(files, cfg.quota_auth_index)
            response = client.post(f"{url}/api-call", json={
                "auth_index": auth_index, "method": "GET", "url": ENDPOINT,
                "header": {"Authorization": "Bearer $TOKEN$",
                           "anthropic-version": "2023-06-01",
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
