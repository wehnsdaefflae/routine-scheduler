"""Sign a subscription account back in through CLIProxyAPI, from the console.

The proxy owns the OAuth flow (docs/claude-proxy-cutover.md): it mints the consent URL,
receives the authorization code and writes the auth file. What the console adds is the
HANDOFF for a browser that is not on the server. The consent page redirects to
`http://localhost:54545/callback` (Claude; 1455 for Codex) — `localhost` on the OPERATOR'S
device, where nothing listens, so the page fails to load. The address bar still carries the
code and the state; the operator pastes that address (or the code the page shows) back into
the console, and the proxy's management callback completes the exchange.

Everything the management key sees stays on the server, like `cliproxy_quota`: the dicts
returned here carry a consent URL, a state, a status word and the proxy's own one-line
account messages — never a token, a key or an upstream body. Every public function returns
a soft `{"ok": False, "error": …}` instead of raising, so the card can show the reason.
Which endpoint a call is made for is `cliproxy_mgmt`'s business: it resolves the binding
(one key per proxy) and names the providers that endpoint's models belong to.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from ..config import EndpointConfig
from .base import EndpointError
from .cliproxy_mgmt import LABEL, LOGIN_PROVIDER
from .cliproxy_mgmt import client as _client

# login-route provider name → card label (the auth-file names are cliproxy_mgmt's)
PROVIDERS = {login: LABEL[auth] for auth, login in LOGIN_PROVIDER.items()}
CALLBACK_PORT = {"anthropic": 54545, "codex": 1455}
STATUS_POLL_S = 20        # how long `complete` waits for the proxy's token exchange
# a token-shaped word: 40+ characters of the base64/JWT/API-key alphabet and nothing else —
# a JSON fragment or a sentence never matches, a leaked credential always does
TOKEN_WORD = re.compile(r"[A-Za-z0-9._~+/=-]{40,}")
UNREACHABLE = "Could not reach the proxy's management API; check connectivity and proxy health."


def _management_error(status_code: int) -> EndpointError:
    return EndpointError(f"Proxy management HTTP {status_code}; check the management key "
                         "and management access configuration.")


def _one_line(text: object, limit: int = 160) -> str:
    """The proxy's own words for an account's state or a refusal, made safe for a card: one
    line, cut, and any token-shaped word dropped (TOKEN_WORD) — the proxy authors these
    messages itself, but a credential that ever strays into one must not reach the console.
    """
    first = str(text or "").splitlines()[0] if text else ""
    words = [w for w in first.split() if not TOKEN_WORD.fullmatch(w)]
    return " ".join(words)[:limit]


def _account_view(f: dict) -> dict:
    """ALLOWLIST projection of one auth file — an unknown key is never copied, so a token
    field the proxy adds tomorrow cannot reach the console.
    """
    return {"name": str(f.get("name") or f.get("id") or ""),
            "provider": str(f.get("provider") or f.get("type") or ""),
            "label": str(f.get("label") or f.get("email") or f.get("account") or ""),
            "status": str(f.get("status") or ""),
            "status_message": _one_line(f.get("status_message")),
            "unavailable": bool(f.get("unavailable")),
            "disabled": bool(f.get("disabled")),
            "next_retry_after": str(f.get("next_retry_after") or "")[:25]}


def accounts(cfg: EndpointConfig, providers=frozenset(), *, timeout: int = 15) -> dict:
    """The proxy's signed-in accounts as ONE endpoint's card may show them: provider,
    label, the proxy's status word and its one-line reason when an account is unavailable —
    filtered to `providers`, the auth-file provider names the asking endpoint's models belong
    to (cliproxy_mgmt.endpoint_providers), with `providers` in the reply naming the sign-in
    controls that card offers. An EMPTY set means unknown, and unknown shows everything: the
    Codex card showing nothing was the defect, not the Claude card showing too much.
    """
    wanted = {p for p in providers if p in LOGIN_PROVIDER}
    try:
        client, url = _client(cfg, timeout)
        with client:
            response = client.get(f"{url}/auth-files")
            if response.status_code != 200:
                raise _management_error(response.status_code)
            listing = response.json()
            files = listing.get("files") if isinstance(listing, dict) else None
            if not isinstance(files, list):
                raise EndpointError("Proxy account listing has an unrecognised format.")
        rows = [_account_view(f) for f in files if isinstance(f, dict)]
        if wanted:
            rows = [r for r in rows if r["provider"] in wanted]
        offered = sorted(wanted or LOGIN_PROVIDER)
        return {"supported": True, "ok": True, "accounts": rows,
                "providers": [{"id": LOGIN_PROVIDER[p], "label": LABEL[p]} for p in offered]}
    except EndpointError as exc:
        return {"supported": True, "ok": False, "error": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"supported": True, "ok": False, "error": UNREACHABLE}


def start(cfg: EndpointConfig, provider: str, *, timeout: int = 15) -> dict:
    """Ask the proxy for a consent URL. The returned `state` names the pending sign-in;
    `complete` needs it back, and the callback refuses a code for any other state.
    """
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"unknown provider {provider!r} — one of "
                                      f"{', '.join(PROVIDERS)}"}
    try:
        client, url = _client(cfg, timeout)
        with client:
            response = client.get(f"{url}/{provider}-auth-url", params={"is_webui": "true"})
            if response.status_code != 200:
                raise _management_error(response.status_code)
            reply = response.json()
        if (not isinstance(reply, dict) or reply.get("status") != "ok"
                or not reply.get("url") or not reply.get("state")):
            why = _one_line(reply.get("error")) if isinstance(reply, dict) else ""
            raise EndpointError("The proxy did not return a sign-in link"
                                + (f": {why}" if why else "."))
        return {"ok": True, "provider": provider, "url": str(reply["url"]),
                "state": str(reply["state"]), "callback_port": CALLBACK_PORT[provider]}
    except EndpointError as exc:
        return {"ok": False, "error": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"ok": False, "error": UNREACHABLE}


def parse_response(pasted: str, state: str) -> tuple[str, str]:
    """What the operator pasted → (code, state). Three shapes are accepted: the whole
    callback address (`http://localhost:54545/callback?code=…&state=…` — the page that
    failed to load on their device), `code#state` as a consent page shows it, or the bare
    code. A state inside the paste must be THIS sign-in's — a code from an older attempt
    would be refused by the proxy anyway, and the refusal should name the reason.
    """
    text = pasted.strip()
    if not text:
        raise EndpointError("Paste the address of the page you landed on, or the code it "
                            "showed.")
    code, seen_state = text, ""
    if "://" in text:
        query = parse_qs(urlsplit(text).query)
        if query.get("error"):
            detail = query.get("error_description", [""])[0]
            raise EndpointError(f"The sign-in page reported {query['error'][0]!r}"
                                + (f": {detail}" if detail else ""))
        code = query.get("code", [""])[0]
        seen_state = query.get("state", [""])[0]
        if not code:
            raise EndpointError("That address carries no code — copy the WHOLE address of "
                                "the page you landed on after signing in.")
    elif "#" in text:
        code, seen_state = text.split("#", 1)
    if seen_state and seen_state != state:
        raise EndpointError("That code belongs to a different sign-in attempt — start "
                            "again and paste what the new page gives you.")
    return code.strip(), state


def complete(cfg: EndpointConfig, provider: str, state: str, pasted: str, *,
             timeout: int = 15, poll_s: float = STATUS_POLL_S, sleep=time.sleep) -> dict:
    """Hand the code to the proxy and wait for its token exchange. The callback answers
    `ok` as soon as it has the code; whether the exchange WORKED is `get-auth-status`, polled
    until it leaves `wait` — so a wrong code fails here, with the proxy's reason, instead of
    reading as a success the account list then contradicts.
    """
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"unknown provider {provider!r} — one of "
                                      f"{', '.join(PROVIDERS)}"}
    try:
        code, state = parse_response(pasted, state)
        client, url = _client(cfg, timeout)
        with client:
            response = client.post(f"{url}/oauth-callback",
                                   json={"provider": provider, "state": state, "code": code})
            reply = response.json() if response.status_code == 200 else {}
            if not isinstance(reply, dict) or reply.get("status") != "ok":
                why = _one_line(reply.get("error")) if isinstance(reply, dict) else ""
                raise EndpointError("The proxy refused the code"
                                    + (f": {why}" if why else
                                       f" (HTTP {response.status_code})")
                                    + " — start the sign-in again.")
            deadline = time.monotonic() + poll_s
            while True:
                poll = client.get(f"{url}/get-auth-status", params={"state": state})
                verdict = poll.json() if poll.status_code == 200 else {}
                word = verdict.get("status") if isinstance(verdict, dict) else None
                if word == "ok":
                    return {"ok": True, "provider": provider}
                if word == "error":
                    raise EndpointError("The proxy could not finish the sign-in: "
                                        + (_one_line(verdict.get("error")) or "unknown error"))
                if word != "wait" or time.monotonic() >= deadline:
                    raise EndpointError("The proxy has not confirmed the sign-in yet — check "
                                        "the account list in a moment; if it still reads "
                                        "unavailable, start the sign-in again.")
                sleep(1)
    except EndpointError as exc:
        return {"ok": False, "error": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"ok": False, "error": UNREACHABLE}
