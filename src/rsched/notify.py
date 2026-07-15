"""The ONE outbound user-notification seam — every implicit "reach the user" send goes
through here, engine and daemon alike.

The contact contract (docs/notifications.md): an agent contacts the user through exactly
one primitive — the durable record on the web console (a decision on the Decisions page,
a chat reply, the run stream). DELIVERY channels fan out from that record and are
user-selected:

  - web        — always on: Decisions page, in-app notifications, opt-in browser push
                 (web/push.py keys off the same open-decisions source of truth).
  - discord    — opt-in per routine/conversation via the `communication` permission,
                 which reserves the `discord` util. The ENGINE mirrors blocking decisions
                 (engine/decisions.py) and the DAEMON pings on background-task delivery
                 (daemon/detached.py); both send through this module.

Any OTHER channel (zulip, email, …) is an explicit util call by the agent itself —
granted, visible in the transcript, never engine-implicit.

Everything here is best-effort: a missing or broken channel degrades to web-only and
never blocks a run or the daemon.
"""

from __future__ import annotations

import logging

from . import utils_lib

log = logging.getLogger("rsched.notify")

UTIL_TIMEOUT_S = 25
_CHANNEL_UTIL = "discord"
_CHANNEL_PERMISSION = "communication"


def discord_enabled(server, *, granted_utils=None, permissions=None) -> bool:
    """Whether the Discord channel is ON for this routine/conversation. The engine passes
    the run policy's reserved-util set (`granted_utils`); the daemon, which has only raw
    routine.yaml at hand, passes the held-permissions list. Either gate plus a present
    util is required — a doc without its util (or vice versa) degrades to web-only.
    """
    if granted_utils is not None and _CHANNEL_UTIL not in granted_utils:
        return False
    if permissions is not None and _CHANNEL_PERMISSION not in permissions:
        return False
    return utils_lib.exists(server.utils_home, _CHANNEL_UTIL)


def run_channel(server, args: list[str], timeout: int = UTIL_TIMEOUT_S) -> tuple[int, str]:
    """Run the channel util once, best-effort: (exit code, stdout). Never raises — a
    notification failure is logged and swallowed, the durable web record already exists.
    """
    try:
        code, out, err = utils_lib.run_util(server.utils_home, _CHANNEL_UTIL, args,
                                            timeout=timeout)
    except Exception as exc:  # a channel must never take the caller down
        log.warning("notify channel: %s", exc)
        return 1, ""
    if code != 0:
        log.warning("notify channel: exit %s: %s", code, (err or out)[:200])
    return code, out


def send(server, text: str, *, title: str = "") -> bool:
    """One outbound message to the user's Discord channel. Returns False when the channel
    is down (callers already have the web record; nothing else to do).
    """
    args = ["send", text] + (["--title", title] if title else [])
    code, _ = run_channel(server, args)
    return code == 0
