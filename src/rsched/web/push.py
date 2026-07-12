"""Web Push (tier-2 browser notifications): VAPID keys, the per-browser subscription
store, and the decision sender the daemon drives off the event bus.

Opt-in like the Discord mirror: nothing is sent until a browser subscribes (Settings →
Notifications). State lives in the config dir (mounted in Docker, never inside a routine):
`vapid-private.pem` (generated on first use), `push-subscriptions.json` (one entry per
browser), `push-notified.json` (qids already pushed — the sender's dedupe memory).
A dead subscription (push service answers 404/410) is dropped on sight. Everything is
best-effort: push failures never disturb the daemon."""

from __future__ import annotations

import base64
import json
import logging
import threading
from pathlib import Path

from ..paths import atomic_write_json, config_file, read_json

log = logging.getLogger("rsched.push")

_SUBS_FILE = "push-subscriptions.json"
_NOTIFIED_FILE = "push-notified.json"
_VAPID_FILE = "vapid-private.pem"
_NOTIFIED_CAP = 500
_VAPID_SUB = "mailto:ops@routine-scheduler.local"
_lock = threading.Lock()   # subscriptions + notified state are read-modify-write files


def push_dir(server) -> Path:
    """Where push state lives: next to config.yaml (server.source), so a container keeps
    it across restarts via the mounted config dir."""
    return (server.source.parent if getattr(server, "source", None)
            else config_file().parent)


# ---- VAPID keys --------------------------------------------------------------------------------


def vapid_public_key(server) -> str:
    """The applicationServerKey browsers subscribe with (urlsafe-b64, no padding) —
    generating and persisting the private key on first use."""
    from py_vapid import Vapid, b64urlencode
    from cryptography.hazmat.primitives import serialization

    path = push_dir(server) / _VAPID_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        v = Vapid.from_file(str(path))
    else:
        v = Vapid()
        v.generate_keys()
        v.save_key(str(path))
        log.info("push: generated VAPID keypair at %s", path)
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


# ---- the subscription store --------------------------------------------------------------------


def _subs_path(server) -> Path:
    return push_dir(server) / _SUBS_FILE


def subscriptions(server) -> list[dict]:
    subs = read_json(_subs_path(server))
    return subs if isinstance(subs, list) else []


def add_subscription(server, subscription: dict) -> int:
    """Upsert by endpoint (a browser re-subscribing replaces its old entry). Returns count."""
    with _lock:
        subs = [s for s in subscriptions(server)
                if s.get("endpoint") != subscription.get("endpoint")]
        subs.append(subscription)
        atomic_write_json(_subs_path(server), subs)
        return len(subs)


def remove_subscription(server, endpoint: str) -> int:
    with _lock:
        subs = [s for s in subscriptions(server) if s.get("endpoint") != endpoint]
        atomic_write_json(_subs_path(server), subs)
        return len(subs)


# ---- sending -----------------------------------------------------------------------------------


def _send_one(server, subscription: dict, payload: dict) -> bool:
    """Push one payload to one browser. Returns False when the subscription is dead
    (removed on the spot); transient failures just log."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=str(push_dir(server) / _VAPID_FILE),
                vapid_claims={"sub": _VAPID_SUB})
        return True
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            remove_subscription(server, subscription.get("endpoint", ""))
            log.info("push: dropped dead subscription (%s)", code)
        else:
            log.warning("push: send failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 — pushes must never disturb the daemon
        log.warning("push: send failed: %s", exc)
        return False


def send_to_all(server, payload: dict) -> int:
    """Fan one payload out to every subscribed browser; returns successful sends."""
    return sum(_send_one(server, s, payload) for s in subscriptions(server))


def notify_new_decisions(server) -> int:
    """The sender the bus listener calls: diff the instance's open decisions against the
    already-pushed set and push one notification per NEW one. Same source of truth as the
    Decisions page (api_questions.open_decisions), so the surfaces can never disagree.
    Cheap no-op while nobody is subscribed."""
    if not subscriptions(server):
        return 0
    from .api_questions import open_decisions

    qs = [q for q in open_decisions(server) if q.get("qid") and not q.get("answered")]
    with _lock:
        notified = read_json(push_dir(server) / _NOTIFIED_FILE)
        notified = notified if isinstance(notified, list) else []
        known = set(notified)
        fresh = [q for q in qs if q["qid"] not in known]
        if not fresh:
            return 0
        notified = (notified + [q["qid"] for q in fresh])[-_NOTIFIED_CAP:]
        atomic_write_json(push_dir(server) / _NOTIFIED_FILE, notified)
    sent = 0
    for q in fresh:
        body = str(q.get("question") or "").replace("\n", " ")[:160]
        sent += send_to_all(server, {
            "title": f"decision needed · {q.get('routine', '?')}",
            "body": body,
            "tag": f"rsched-{q['qid']}",
            "url": "/#/questions",
        })
    return sent


async def bus_listener(server, bus) -> None:
    """Daemon-side subscriber: any bus event may mean a new decision exists (a run parked
    on a blocking ask, a finished run that filed deferred ones, a wizard asking) — debounce
    briefly, then diff-and-push off the event loop. Runs for the daemon's lifetime."""
    import asyncio

    with bus.subscribe() as q:
        while True:
            await q.get()
            try:
                while True:   # coalesce the burst a finishing run produces
                    await asyncio.wait_for(q.get(), timeout=2.0)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            try:
                await asyncio.to_thread(notify_new_decisions, server)
            except Exception as exc:  # noqa: BLE001
                log.warning("push: notify pass failed: %s", exc)
