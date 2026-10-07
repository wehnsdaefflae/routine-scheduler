"""The agent desktops as the OPERATOR sees them (docs/desktop-sessions.md): every running
desktop — one VM per routine, conversation or background task — with the token that opens its
screen, and the one act the console takes on one.

  GET  /api/desktops              — the fleet, from the broker's operator-only `/fleet`
  POST /api/desktops/{name}/stop  — power one desktop off now (`/fleet_stop`)

The broker sits behind TWO secrets, and this route is the only caller holding both: the bearer
its door checks (`DESKTOP_VM_TOKEN`, which every desktop-holding routine is granted too) and the
operator header its fleet view checks (`DESKTOP_OPERATOR_TOKEN`, which no routine is). The fleet
carries each desktop's `vnc` token, the key to that desktop's screen through `/desktop-view`, so
the GET is OPERATOR-ONLY on this side as well: it is in `app.ROUTINE_TOKEN_DENIED_READS`, and the
stop is a mutation, which the routine token never makes.

Neither route rides a bus event — the broker publishes nothing onto the bus — so there is no
read model to memoize: the console's one reader (static/desktops-store.js) polls on an interval,
and only while a tab is visible and something on it is showing a desktop.
"""

from __future__ import annotations

import logging
import re

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request

from ..paths import read_yaml
from .screen_proxy import DESKTOP, SCREEN_TOKEN

log = logging.getLogger("rsched.web.desktops")

router = APIRouter(tags=["desktops"])

#: The bearer the broker's door checks — the routines' token, held here too.
VM_TOKEN_KEY = "DESKTOP_VM_TOKEN"  # noqa: S105 — a store KEY, not a secret value

#: `<home>--<slug>`: the broker names a desktop after the directory its caller works in
#: (deploy/desktop-broker/proof.py), so the name says whose computer it is.
NAME = re.compile(r"(routines|conversations|background)--([A-Za-z0-9][A-Za-z0-9._-]{0,80})")

#: A slug the console can route to: `#/routine/…` and `#/conversations/…` accept no other.
ROUTABLE = re.compile(r"[a-z0-9-]+")

#: The broker answers from memory; a slow one is wedged, and a console worker is not the
#: place to wait that out.
BROKER_TIMEOUT_S = 10.0


def _broker_headers() -> dict[str, str]:
    """Both of the broker's credentials, from the central secrets store — or a 503 that names
    the missing one, which is the only thing that can be done about it.
    """
    from ..secrets import load_secrets

    try:
        store = load_secrets()
    except OSError:
        store = {}
    vm = (store.get(VM_TOKEN_KEY) or "").strip()
    operator = (store.get(DESKTOP.secret_key) or "").strip()
    missing = [key for key, value in ((VM_TOKEN_KEY, vm), (DESKTOP.secret_key, operator))
               if not value]
    if missing:
        raise HTTPException(503, f"the desktop broker needs {' and '.join(missing)} in "
                                 "Settings → Secrets — the same values the desktop service "
                                 "was started with")
    return {"Authorization": f"Bearer {vm}", "X-Desktop-Operator": operator,
            "Content-Type": "application/json"}


async def _broker(request: Request, op: str, body: dict) -> dict:
    """POST one operator operation to the broker and return its JSON reply.

    Every failure is a 502 that names the broker and what it said: a Desktops page that is
    merely empty when the broker is down would read as "nothing is running".
    """
    base = request.app.state.server.desktop_broker_url.strip().rstrip("/")
    if not base:
        raise HTTPException(503, "no desktop broker is configured — set desktop_broker_url "
                                 "in Settings → server process")
    headers = _broker_headers()
    url = f"{base}/{op}"
    try:
        # no redirect is ever a hop worth following: the broker is a JSON door on a private
        # network, and a followed redirect would be a request it chose rather than this route
        async with httpx.AsyncClient(timeout=BROKER_TIMEOUT_S, follow_redirects=False) as client:
            reply = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("desktops: broker %s unreachable: %s", url, exc)
        raise HTTPException(502, f"the desktop broker did not answer: {exc}") from exc
    try:
        data = reply.json()
    except ValueError:
        data = {}
    if reply.status_code != 200 or not isinstance(data, dict):
        said = (data.get("error") if isinstance(data, dict) else "") or reply.text[:200]
        raise HTTPException(502, f"the desktop broker refused /{op} "
                                 f"({reply.status_code}): {said}")
    return data


def _background_owner(server, task: str) -> str:
    """The conversation a background task belongs to ('' when it cannot be told)."""
    try:
        cfg = read_yaml(server.background_home / task / "routine.yaml", {})
    except (OSError, yaml.YAMLError):   # a gone or broken task file only loses its link
        return ""
    owner = cfg.get("owner") if isinstance(cfg, dict) else None
    slug = str((owner or {}).get("slug") or "") if isinstance(owner, dict) else ""
    return slug if ROUTABLE.fullmatch(slug) else ""


def owner_of(server, name: str) -> dict:
    """Whose desktop this is, and where the console shows that owner."""
    m = NAME.fullmatch(name or "")
    if not m:
        return {"kind": "", "slug": name, "href": ""}
    home, slug = m.groups()
    routable = bool(ROUTABLE.fullmatch(slug))
    if home == "routines":
        return {"kind": "routine", "slug": slug,
                "href": f"#/routine/{slug}" if routable else ""}
    if home == "conversations":
        return {"kind": "conversation", "slug": slug,
                "href": f"#/conversations/{slug}" if routable else ""}
    conversation = _background_owner(server, slug)
    return {"kind": "background", "slug": slug,
            "href": f"#/conversations/{conversation}" if conversation else ""}


def _row(server, raw: dict) -> dict:
    name = str(raw.get("name") or "")
    vnc = str(raw.get("vnc") or "")
    folders = raw.get("folders")
    if not isinstance(folders, list):
        folders = []
    return {"name": name, "owner": owner_of(server, name),
            "slot": raw.get("slot"), "ready": bool(raw.get("ready")),
            "stopping": bool(raw.get("stopping")),
            "up_s": int(raw.get("up_s") or 0), "idle_s": int(raw.get("idle_s") or 0),
            # a token of any other shape could never open the screen (the relay refuses it),
            # so it is dropped here rather than handed to a frame that would fail later
            "vnc": vnc if SCREEN_TOKEN.fullmatch(vnc) else "",
            "folders": [f for f in folders if isinstance(f, dict)]}


@router.get("/desktops")
async def list_desktops(request: Request) -> dict:
    """Every running desktop, most recently active first, each with its owner's console link
    and its screen token.
    """
    data = await _broker(request, "fleet", {})
    server = request.app.state.server
    rows = [_row(server, d) for d in data.get("desktops") or [] if isinstance(d, dict)]
    rows.sort(key=lambda r: (r["idle_s"], r["name"]))
    return {"slots": data.get("slots"), "idle_limit_s": data.get("idle_limit_s"),
            "desktops": rows}


@router.post("/desktops/{name}/stop")
async def stop_desktop(request: Request, name: str) -> dict:
    """Power one desktop off now. Its owner's NEXT desktop command starts it again — a stop
    takes a computer back, it does not take the permission away.
    """
    if not NAME.fullmatch(name):
        raise HTTPException(400, f"{name!r} is not a desktop name (<home>--<slug>)")
    data = await _broker(request, "fleet_stop", {"name": name})
    return {"ok": True, "stopped": bool(data.get("stopped"))}
