"""The REAL Claude subscription quota — what is left of the 5-hour and 7-day windows.

Operator, 2026-09-05: "we should be able to see the remaining percentages of our claude code
subscription. why don't i?" The honest answer had two halves, and this module is the fix for both.

**There is a real source.** `GET https://api.anthropic.com/api/oauth/usage` reports per-window
`utilization` for the windows Claude Code actually enforces — the same numbers claude.ai's usage
panel and the CLI statusline render. It is UNDOCUMENTED and Anthropic may change it without
notice, so every failure here is soft: the card says what went wrong, nothing raises, and no
scheduling decision is ever taken from it.

**The console was rendering something else.** D33 shipped a LOCAL PROXY — tokens this instance
burned through claude-cli endpoints in rolling windows — as muted 11px text on the Settings
endpoint card. It could not answer "% remaining" even in principle: Anthropic's windows are not a
token count, the proxy is blind to the operator's own interactive sessions and to claude.ai on the
same subscription, it drops cache traffic (the most quota-expensive input class), and it is bounded
by run retention. A percentage derived from it would be a fabricated number wearing a percent sign,
so it is deleted rather than patched.

**Two tokens, not one.** The usage endpoint needs the `user:profile` scope. The headless
`claude setup-token` that `$CLAUDE_CODE_OAUTH_TOKEN` holds carries `user:inference` only and 403s
`oauth_scope_insufficient`. An interactive `claude /login` mints a full-scope token into
`~/.claude/.credentials.json`, which is the only credential that can answer this — so it is read
from there and NEVER from the inference ladder (`claude_cli_wire.resolve_token`), which stays
exactly as it was.

**Expiry is reported, not discovered by a 401.** The credentials file carries `expiresAt`, and
nothing refreshes it headlessly: the daemon runs the CLI in print mode off the env token, so the
login token simply ages out (the live instance's had been expired for four days when this was
built, silently). The probe reads the stamp and says so, with the one-line fix, instead of showing
an authentication error that does not name its own cause.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
MANAGE_URL = "https://claude.ai/settings/usage"
CREDENTIALS_FILE = "~/.claude/.credentials.json"
#: The windows Claude Code enforces, in the order a reader wants them. `seven_day_opus` is
#: included because this instance's system model is opus and the plan may report a fourth bucket;
#: an absent window is simply not rendered.
WINDOWS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus")
#: One-line fix, shown verbatim wherever the credential is missing or stale. It names the CONTAINER
#: because that is where the daemon reads the file from, and a login on the host does not reach it.
RELOGIN = ("run `claude /login` once inside the rsched container — the headless setup-token "
           "carries user:inference only, and an interactive login mints the user:profile token "
           "this reads (it expires, and nothing refreshes it headlessly)")


def _credentials(path: str = CREDENTIALS_FILE) -> dict:
    from ..paths import expand

    try:
        with expand(path).open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    oauth = data.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) else {}


def profile_token(path: str = CREDENTIALS_FILE) -> tuple[str | None, str]:
    """The full-scope token and WHY it is unusable when it is. Never returns the token in the
    reason — this is the one place the web layer touches that file, and it reads one field.
    """
    oauth = _credentials(path)
    token = oauth.get("accessToken")
    if not (isinstance(token, str) and token.strip()):
        return None, f"no interactive login on this host ({CREDENTIALS_FILE} has no token) — "
    expires = oauth.get("expiresAt")
    if isinstance(expires, int | float):
        when = datetime.fromtimestamp(expires / 1000, UTC)
        if when <= datetime.now(UTC):
            days = (datetime.now(UTC) - when).days
            return None, (f"the login token expired {when:%Y-%m-%d} "
                          f"({days} day{'s' if days != 1 else ''} ago) — ")
    return token.strip(), ""


def expires_at(path: str = CREDENTIALS_FILE) -> str | None:
    """When the login token runs out, ISO — so the card can warn BEFORE it 401s."""
    expires = _credentials(path).get("expiresAt")
    if isinstance(expires, int | float):
        return datetime.fromtimestamp(expires / 1000, UTC).isoformat()
    return None


def normalize(raw: dict, *, now: datetime | None = None) -> dict:
    """The raw payload reduced to the windows we render. `utilization` is percent USED, so
    `remaining` is its complement — which is the number the operator actually asked for.
    """
    now = now or datetime.now(UTC)
    out: dict[str, dict] = {}
    for name in WINDOWS:
        bucket = raw.get(name)
        if not isinstance(bucket, dict) or bucket.get("utilization") is None:
            continue
        used = float(bucket["utilization"])
        row = {"utilization": round(used, 2), "remaining": round(100.0 - used, 2),
               "resets_at": bucket.get("resets_at") or "", "seconds_until_reset": None}
        if row["resets_at"]:
            try:
                # the API stamps are Z-suffixed; fromisoformat takes that since 3.11
                when = datetime.fromisoformat(str(row["resets_at"]))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                row["seconds_until_reset"] = max(0, int((when - now).total_seconds()))
            except ValueError:
                pass
        out[name] = row
    return out


def read_quota(*, timeout: int = 15, path: str = CREDENTIALS_FILE) -> dict:
    """`{ok, windows, extra_usage, manage_url, error}` — never raises.

    Fail-soft is the whole discipline: this is a panel, and a panel that can 500 the Settings
    page over an undocumented third-party endpoint is worse than one that says it does not know.
    """
    base = {"supported": True, "manage_url": MANAGE_URL, "expires_at": expires_at(path)}
    token, why = profile_token(path)
    if token is None:
        return {**base, "ok": False, "error": f"{why}{RELOGIN}"}
    try:
        resp = httpx.get(ENDPOINT, timeout=timeout, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
        })
    except httpx.HTTPError as exc:
        return {**base, "ok": False, "error": f"could not reach the usage API: {exc}"}
    if resp.status_code == 401:
        return {**base, "ok": False, "error": f"the login token is no longer accepted — {RELOGIN}"}
    if resp.status_code == 403:
        return {**base, "ok": False,
                "error": "this token lacks the user:profile scope (it is the headless "
                         f"setup-token, not an interactive login) — {RELOGIN}"}
    if resp.status_code != 200:
        return {**base, "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    try:
        raw = resp.json()
    except ValueError:
        return {**base, "ok": False, "error": "the usage API returned a body that is not JSON"}
    windows = normalize(raw if isinstance(raw, dict) else {})
    if not windows:
        return {**base, "ok": False,
                "error": "the usage API answered with no recognisable window — its shape may "
                         "have changed (it is undocumented)"}
    extra = raw.get("extra_usage") if isinstance(raw, dict) else None
    return {**base, "ok": True, "windows": windows,
            "extra_usage": extra if isinstance(extra, dict) else None}
