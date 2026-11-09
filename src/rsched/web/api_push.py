"""Web Push subscription API: the browser fetches the VAPID public key, registers its
subscription, and can drop it or fire a test push. All state handling lives in push.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import push

router = APIRouter(tags=["push"])


@router.get("/push")
def push_info(request: Request) -> dict:
    server = request.app.state.server
    try:
        key = push.vapid_public_key(server)
    except Exception as exc:
        raise HTTPException(503, f"push keys unavailable: {exc}") from exc
    return {"public_key": key, "subscriptions": len(push.subscriptions(server))}


class SubscribeBody(BaseModel):
    subscription: dict


@router.post("/push/subscribe")
def subscribe(request: Request, body: SubscribeBody) -> dict:
    if not body.subscription.get("endpoint"):
        raise HTTPException(400, "subscription needs an endpoint")
    n = push.add_subscription(request.app.state.server, body.subscription)
    return {"ok": True, "subscriptions": n}


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.post("/push/unsubscribe")
def unsubscribe(request: Request, body: UnsubscribeBody) -> dict:
    n = push.remove_subscription(request.app.state.server, body.endpoint)
    return {"ok": True, "subscriptions": n}


@router.post("/push/present")
def present(request: Request) -> dict:
    """The console's presence heartbeat (D280): a tab that is OPEN AND IN THE FOREGROUND says
    so, and the decision sender holds a push back for `push.PRESENCE_S` after the last one.

    Here rather than on `/api/status` — which the console already polls on the same floor —
    because status is read by the dashboard, by `postdeploy`, by the audit and by anything
    that asks what this instance is: a GET that a reader can make must not record that a
    PERSON is present. A background tab must not beat either (the ask is "is he looking at
    this", not "is a browser running"), and only the client knows that.
    """
    at = push.note_presence(request.app.state.server)
    return {"ok": True, "at": at, "window_s": push.PRESENCE_S}


@router.post("/push/test")
def test(request: Request) -> dict:
    """One test notification to every subscribed browser — the Settings page's proof."""
    sent = push.send_to_all(request.app.state.server, {
        "title": "rsched · test notification",
        "body": "Web Push works — decisions will reach this browser.",
        "tag": "rsched-test", "url": "/#/questions"})
    return {"ok": True, "sent": sent}
