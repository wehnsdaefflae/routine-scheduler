"""Read account quota through CLIProxyAPI without reading its OAuth credentials.

The management key is resolved only on the server. The upstream URL is fixed; this
adapter never exposes the management API's general-purpose outbound-call facility.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from ..config import EndpointConfig
from .anthropic_api import API_VERSION
from .base import EndpointError
from .cliproxy_mgmt import auth_files, file_provider
from .cliproxy_mgmt import client as _client
from .subscription_quota import normalize

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
MANAGE_URL = "https://claude.ai/settings/usage"

#: The one status that means "stop asking", as opposed to "something is wrong".
_TOO_MANY_REQUESTS = 429

#: The longest `Retry-After` carried as a countdown (24 h); beyond it the value is almost
#: certainly skew or invention, and "rate limited" alone is more honest.
_RETRY_AFTER_CAP_S = 86_400


def _account(files: list[dict], selected: str) -> str:
    accounts = [row for row in files if file_provider(row) == "claude"
                and not row.get("disabled") and row.get("auth_index")]
    if selected:
        accounts = [row for row in accounts if str(row["auth_index"]) == selected]
    if len(accounts) != 1:
        raise EndpointError("Select one enabled Claude account in the proxy and set its "
                            "quota account index in Settings (blank works for one account).")
    return str(accounts[0]["auth_index"])


def retry_after_seconds(raw: object) -> int | None:
    """`Retry-After` as whole seconds, or None when it says nothing usable.

    Both RFC 9110 forms (delta seconds or an HTTP date), converted once, server-side. None for
    anything unusable (blank, word, non-positive, absurd, past): a wrong countdown is worse than
    none, and a caller must back off on the 429 itself.
    """
    if not isinstance(raw, str) or not (text := raw.strip()):
        return None
    if text.lstrip("+").isdigit():
        secs = int(text)
        return secs if 0 < secs <= _RETRY_AFTER_CAP_S else None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    # a zoneless date is GMT by RFC 9110 (and naive minus aware would raise)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    secs = round((when - datetime.now(tz=UTC)).total_seconds())
    return secs if 0 < secs <= _RETRY_AFTER_CAP_S else None


def _rate_limited(retry_after: object) -> dict:
    """The soft result for a 429: never a figure we could not read, always the FACT."""
    secs = retry_after_seconds(retry_after)
    if secs is None:
        return {"ok": False, "rate_limited": True, "retry_after_s": None,
                "error": "Claude is rate limiting the quota read and gave no Retry-After; "
                         "the figure will reappear once it lets the account read again."}
    return {"ok": False, "rate_limited": True, "retry_after_s": secs,
            "error": f"Claude is rate limiting the quota read — it may be read again in "
                     f"{secs}s. Nothing is wrong with the account or the credential."}


def read_quota(cfg: EndpointConfig, *, timeout: int = 15) -> dict:
    """Return real account windows or a soft, credential-free error for the console.

    A 429 is reported APART (`rate_limited`, `retry_after_s` when known): an ordinary error is
    re-read once fixed, a rate-limited account must be left alone.
    """
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
            # the PROXY itself refusing — its own headers carry the delay
            if response.status_code == _TOO_MANY_REQUESTS:
                return {**base, **_rate_limited(response.headers.get("retry-after"))}
            if response.status_code != 200:
                raise EndpointError(f"Proxy quota request HTTP {response.status_code}.")
            envelope = response.json()
            if not isinstance(envelope, dict):
                raise EndpointError("Proxy quota response has an unrecognised format.")
            status = envelope.get("status_code")
            # …and the usual shape: Claude's own status and headers ride INSIDE the envelope
            if status == _TOO_MANY_REQUESTS:
                head = envelope.get("headers")
                head = head if isinstance(head, dict) else {}
                lower = {str(k).lower(): v for k, v in head.items()}
                return {**base, **_rate_limited(lower.get("retry-after"))}
            if status != 200:
                raise EndpointError(f"Claude usage HTTP {status}; reconnect the account through "
                                    "the proxy with profile access (not a setup-token).")
            raw = envelope.get("body")
            if isinstance(raw, str):
                raw = json.loads(raw)
            # D277: `scoped` = weekly per-model/per-surface limits; `unknown` names an unreadable
            # limit, so "cannot see it" never renders as "not there"
            quota = normalize(raw if isinstance(raw, dict) else {})
            if not quota["windows"] and not quota["scoped"]:
                raise EndpointError("Claude returned no recognisable quota windows.")
            return {**base, "ok": True, "windows": quota["windows"],
                    "scoped": quota["scoped"], "unknown": quota["unknown"],
                    "auth_index": auth_index}
    except EndpointError as exc:
        return {**base, "ok": False, "error": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError, OverflowError):
        # No upstream response bodies or exception URLs: those may contain credentials.
        return {**base, "ok": False,
                "error": "Could not read proxy quota; check connectivity and proxy health."}
