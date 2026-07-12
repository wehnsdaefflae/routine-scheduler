"""The Discord decision surface: mirror a BLOCKING question to the routine's phone
channel and keep both surfaces synchronized — a reply on either side resolves the
decision everywhere, and each side is told when the other decided.

Mirroring is opt-in via the `communication` permission (which reserves the `discord`
util); the engine — not the model — does the mirroring, so every blocking decision of a
communication-enabled routine reaches the channel with the same shape. Everything here is
best-effort: a missing/broken util degrades to the web-only flow, never blocks a run."""

from __future__ import annotations

import json
import logging
import time

from .. import utils_lib

log = logging.getLogger("rsched.decisions")

DISCORD_POLL_S = 20      # how often the wait loop asks the channel for replies
_UTIL_TIMEOUT_S = 25


class DiscordMirror:
    """One blocking question's presence on Discord. Created by `mirror_blocking` (None
    when the routine lacks the permission or the util); then `poll()` inside the wait
    loop and exactly one of `notify_resolved` / `notify_timeout` at the end."""

    def __init__(self, ctx, qid: str):
        self.ctx = ctx
        self.qid = qid
        self.cursor = f"rsched-{ctx.routine.slug}"
        self._next_poll = 0.0
        self._dead = False

    def _run(self, args: list[str]) -> tuple[int, str]:
        try:
            code, out, err = utils_lib.run_util(self.ctx.server.utils_home, "discord",
                                                args, timeout=_UTIL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — the mirror must never take the run down
            log.warning("discord mirror: %s", exc)
            return 1, ""
        if code != 0:
            log.warning("discord mirror: exit %s: %s", code, (err or out)[:200])
        return code, out

    def send_question(self, question: str, options: list[str], default: str,
                      timeout_min: int) -> bool:
        """Post the question; advance the reply cursor first so stale channel chatter is
        never mistaken for the answer. Returns False when the channel is unusable."""
        self._run(["read", "--cursor", self.cursor, "--json"])   # prime: skip old messages
        lines = [f"❓ **{self.ctx.routine.name}** needs a decision:", question.strip()]
        if options:
            lines.append("Options: " + " · ".join(options))
        if default:
            lines.append(f"Without an answer in ~{timeout_min}m I continue with: {default}")
        lines.append("Reply here, or answer on the Decisions page — whichever comes first counts.")
        code, _ = self._run(["send", "\n".join(lines),
                             "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])
        self._dead = code != 0
        return not self._dead

    def poll(self) -> str | None:
        """The newest reply since the cursor, rate-limited to DISCORD_POLL_S."""
        if self._dead or time.monotonic() < self._next_poll:
            return None
        self._next_poll = time.monotonic() + DISCORD_POLL_S
        code, out = self._run(["read", "--cursor", self.cursor, "--json"])
        if code != 0:
            return None
        texts = _reply_texts(out)
        return texts[-1] if texts else None

    def notify_resolved(self, answer: str, source: str) -> None:
        if self._dead:
            return
        note = "✔ got it — acting on your reply." if source == "discord" else \
            f"✔ resolved on the {source or 'web'} console: {answer.strip()[:300]}"
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])

    def notify_timeout(self, default: str) -> None:
        if self._dead:
            return
        note = ("⏳ no answer in time — continuing"
                + (f" with the stated default: {default}" if default else "")
                + ". The question stays open on the Decisions page.")
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])


def _reply_texts(raw: str) -> list[str]:
    """Tolerant parse of `discord read --json` output: a JSON list of strings or of
    objects with a text-ish field; anything else reads as no replies."""
    try:
        data = json.loads(raw.strip() or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("messages") or data.get("replies") or []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("content") or item.get("message") or "").strip()
            if text:
                out.append(text)
    return out


def mirror_blocking(ctx, qid: str, question: str, options: list[str], default: str,
                    timeout_min: int):
    """A live DiscordMirror for this question, or None when the routine is not set up
    for it (no communication permission / no discord util) or the channel is down."""
    g = ctx.grants
    if g is None or "discord" not in g.utils:
        return None
    if not utils_lib.exists(ctx.server.utils_home, "discord"):
        return None
    mirror = DiscordMirror(ctx, qid)
    return mirror if mirror.send_question(question, options, default, timeout_min) else None
