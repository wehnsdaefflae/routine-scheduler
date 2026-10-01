"""Web Push (tier-2 browser notifications): VAPID keys, the per-browser subscription
store, and the decision sender the daemon drives off the event bus.

Opt-in like the Discord mirror: nothing is sent until a browser subscribes (Settings →
Notifications). State lives in the config dir (mounted in Docker, never inside a routine):
`vapid-private.pem` (generated on first use), `push-subscriptions.json` (one entry per
browser), `push-notified.json` (question qids and proposal ids already pushed — the sender's dedupe
memory, and the set it diffs to withdraw a notification once its ask is settled).
A dead subscription (push service answers 404/410) is dropped on sight. Everything is
best-effort: push failures never disturb the daemon.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ..paths import atomic_write, atomic_write_json, config_file, file_lock, read_json

log = logging.getLogger("rsched.push")

_SUBS_FILE = "push-subscriptions.json"
_NOTIFIED_FILE = "push-notified.json"
_VAPID_FILE = "vapid-private.pem"
_NOTIFIED_CAP = 500
_VAPID_SUB = "mailto:ops@routine-scheduler.local"
# pywebpush defaults ttl=0, which tells the push service to deliver ONLY to a device
# connected at that instant and otherwise DISCARD — no queue, no retry. A phone asleep in a
# pocket is exactly that device, so the notification an away operator most needs is the one
# most reliably thrown away, invisibly (the send still returns 201). A decision waiting on a
# human stays worth reading a day later, so ask the service to hold it that long instead.
_TTL_SECONDS = 24 * 60 * 60
_lock = threading.Lock()   # subscriptions + notified state are read-modify-write files
#: The listener's debounce: a burst ends after this much bus silence…
_QUIET_S = 2.0
#: …or this long after it began, whichever comes first. Without the cap a busy fleet never
#: falls silent for two seconds — every LLM call of every live run is a bus event — so a
#: decision waited for the WHOLE fleet to go quiet before its push left.
_MAX_COALESCE_S = 10.0


def push_dir(server) -> Path:
    """Where push state lives: next to config.yaml (server.source), so a container keeps
    it across restarts via the mounted config dir.
    """
    return (server.source.parent if getattr(server, "source", None)
            else config_file().parent)


# ---- VAPID keys --------------------------------------------------------------------------------


def vapid_public_key(server) -> str:
    """The applicationServerKey browsers subscribe with (urlsafe-b64, no padding) —
    generating and persisting the private key on first use.

    First use is checked and generated under a file lock and written atomically (0600).
    `Vapid.save_key` is a plain open-and-write at the umask's mode: two first requests at
    once (the Settings page and a re-subscribing tab) each generated a pair and the later
    save replaced the earlier, so one browser subscribed against a public key whose private
    half no longer existed and every push to it failed; a reader between open and write
    parsed an empty PEM; and the private key landed world-readable.
    """
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid, b64urlencode

    path = push_dir(server) / _VAPID_FILE
    with file_lock(path.with_name(f".{path.name}.lock")):
        if path.exists():
            v = Vapid.from_file(str(path))
        else:
            v = Vapid()
            v.generate_keys()
            atomic_write(path, v.private_pem())
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
    (removed on the spot); transient failures just log.
    """
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=str(push_dir(server) / _VAPID_FILE),
                vapid_claims={"sub": _VAPID_SUB},
                ttl=_TTL_SECONDS)
        return True
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            remove_subscription(server, subscription.get("endpoint", ""))
            log.info("push: dropped dead subscription (%s)", code)
        else:
            log.warning("push: send failed: %s", exc)
        return False
    except Exception as exc:
        log.warning("push: send failed: %s", exc)
        return False


def send_to_all(server, payload: dict) -> int:
    """Fan one payload out to every subscribed browser; returns successful sends."""
    return sum(_send_one(server, s, payload) for s in subscriptions(server))


def _open_asks(server) -> list[tuple[str, str, str]]:
    """What waits on a person, as (key, title, text): every open unanswered QUESTION (keyed by
    its qid) and every standing PROPOSAL (keyed by its `pc-` id — the two id spaces never
    meet). The same two lists the Decisions page and the header badge read, so the surfaces
    can never disagree; proposals were missing here until 2026-10, so a queued creation or a
    met goal reached nobody away from the console.
    """
    from .decisions_read import open_decisions, open_proposals

    asks = [(str(q["qid"]), f"decision needed · {q.get('routine', '?')}",
             str(q.get("question") or ""))
            for q in open_decisions(server) if q.get("qid") and not q.get("answered")]
    asks += [(str(p["id"]), f"proposal · {p.get('routine') or '?'}", str(p.get("summary") or ""))
             for p in open_proposals(server)]
    return asks


def notify_new_decisions(server) -> int:
    """The sender the bus listener calls: diff what waits on a person (`_open_asks`) against
    the already-pushed set and push one notification per NEW one. Cheap no-op while nobody is
    subscribed.
    """
    if not subscriptions(server):
        return 0
    asks = _open_asks(server)
    open_keys = {key for key, _, _ in asks}
    with _lock:
        notified = read_json(push_dir(server) / _NOTIFIED_FILE)
        notified = notified if isinstance(notified, list) else []
        known = set(notified)
        fresh = [ask for ask in asks if ask[0] not in known]
        # An ask we already pushed that is no longer open has been answered, decided or
        # withdrawn: send a same-tag "close" push so the phone notification is retracted, and
        # drop the key so an ask that re-opens later notifies afresh.
        stale = [key for key in notified if key not in open_keys]
        if not fresh and not stale:
            return 0
        stale_set = set(stale)
        notified = ([key for key in notified if key not in stale_set]
                    + [key for key, _, _ in fresh])[-_NOTIFIED_CAP:]
        atomic_write_json(push_dir(server) / _NOTIFIED_FILE, notified)
    sent = 0
    for key, title, text in fresh:
        sent += send_to_all(server, {
            "title": title,
            "body": text.replace("\n", " ")[:160],
            "tag": f"rsched-{key}",
            "url": "/#/questions",
        })
    for key in stale:
        # `close: true` tells the service worker to clear the tray notification with this tag
        # rather than show a new one (sw.js) — the settled ask's alert is retracted.
        sent += send_to_all(server, {"tag": f"rsched-{key}", "close": True})
    return sent


async def bus_listener(server, bus) -> None:
    """Daemon-side subscriber: any bus event may mean a new decision exists (a run parked
    on a blocking ask, a finished run that filed deferred ones, a clarify session asking) — debounce
    briefly (`_QUIET_S`, never longer than `_MAX_COALESCE_S`), then diff-and-push off the event
    loop. Runs for the daemon's lifetime.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    with bus.subscribe() as q:
        while True:
            await q.get()
            deadline = loop.time() + _MAX_COALESCE_S
            # coalesce the burst a finishing run produces — but only so long
            while (left := deadline - loop.time()) > 0:
                try:
                    await asyncio.wait_for(q.get(), timeout=min(_QUIET_S, left))
                except TimeoutError:
                    break
            try:
                await asyncio.to_thread(notify_new_decisions, server)
            except Exception as exc:
                log.warning("push: notify pass failed: %s", exc)
