"""The Discord decision surface: mirror a BLOCKING question to the routine's phone
channel and keep both surfaces synchronized — a reply on either side resolves the
decision everywhere, and each side is told when the other decided.

Mirroring is opt-in via the `communication` permission (which reserves the `discord`
util); the engine — not the model — does the mirroring, so every blocking decision of a
communication-enabled routine reaches the channel with the same shape. All sends go
through the ONE outbound seam (rsched.notify) and are best-effort: a missing/broken
channel degrades to the web-only flow, never blocks a run.
"""

from __future__ import annotations

import json
import time

from .. import notify

DISCORD_POLL_S = 20      # how often the wait loop asks the channel for replies


class DiscordMirror:
    """One blocking question's presence on Discord. Created by `mirror_blocking` (None
    when the routine lacks the permission or the util); then `poll()` inside the wait
    loop and exactly one of `notify_resolved` / `notify_timeout` at the end.
    """

    def __init__(self, ctx, qid: str):
        self.ctx = ctx
        self.qid = qid
        self.cursor = f"rsched-{ctx.routine.slug}"
        self.question_id = 0     # snowflake of the posted question; poll accepts only newer
        self._next_poll = 0.0
        self._dead = False

    def _run(self, args: list[str]) -> tuple[int, str]:
        return notify.run_channel(self.ctx.server, args)

    def send_question(self, question: str, options: list[str], default: str,
                      timeout_min: int) -> bool:
        """Post the question and remember its message id (a Discord snowflake): poll()
        accepts only replies POSTED AFTER it — F194: a stale or another routine's message
        must never settle a fresh question (the cursor prime alone silently failed to
        guarantee that). Sending with --cursor also feeds the util's sent-ledger, so
        `read --mine` can skip replies addressed to a sibling routine's messages.
        Returns False when the channel is unusable.
        """
        self._run(["read", "--cursor", self.cursor, "--json"])   # prime: skip old messages
        lines = [f"❓ **{self.ctx.routine.name}** needs a decision:", question.strip()]
        if options:
            lines.append("Options: " + " · ".join(options))
        if default:
            lines.append(f"Without an answer in ~{timeout_min}m I continue with: {default}")
        lines.append("Reply here, or answer on the Decisions page — whichever comes first counts.")
        code, out = self._run(["send", "\n".join(lines),
                               "--title", f"{self.ctx.routine.slug}: decision {self.qid}",
                               "--cursor", self.cursor, "--json"])
        self._dead = code != 0
        if not self._dead:
            try:
                self.question_id = int(json.loads(out.strip()).get("id") or 0)
            except (ValueError, TypeError, AttributeError):
                self.question_id = 0             # unknown id → guard degrades to cursor-only
        return not self._dead

    def poll(self) -> str | None:
        """The newest reply NEWER than the posted question, rate-limited to DISCORD_POLL_S.
        `--mine` skips replies Discord-addressed to a sibling routine's messages; the
        snowflake guard drops anything posted before the question itself (F194 — observed:
        a 2h-stale "Yes" settled a fresh question on another routine).
        """
        if self._dead or time.monotonic() < self._next_poll:
            return None
        self._next_poll = time.monotonic() + DISCORD_POLL_S
        code, out = self._run(["read", "--cursor", self.cursor, "--mine", "--json"])
        if code != 0:
            return None
        fresh = [text for mid, text in _reply_items(out) if mid > self.question_id]
        return fresh[-1] if fresh else None

    def notify_resolved(self, answer: str, source: str) -> None:
        if self._dead:
            return
        note = "✔ got it — acting on your reply." if source == "discord" else \
            f"✔ resolved on the {source or 'web'} console: {answer.strip()[:300]}"
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])

    def notify_held(self, text: str) -> None:
        """The reply named neither option (D38): tell the channel it is HELD as a normal
        message for the run — delivered after this decision — and the question is still
        open, instead of silently consuming it as approve/decline.
        """
        if self._dead:
            return
        note = ("✋ that names neither option — I'm holding it for the run to read after "
                f"this decision: “{text.strip()[:200]}”. Still waiting — reply approve "
                "or decline.")
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])

    def notify_deferred(self, default: str) -> None:
        if self._dead:
            return
        note = ("↷ deferred to a future run from the console — continuing"
                + (f" with the stated default: {default}" if default else "")
                + ". The question stays open on the Decisions page.")
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])

    def notify_timeout(self, default: str) -> None:
        if self._dead:
            return
        note = ("⏳ no answer in time — continuing"
                + (f" with the stated default: {default}" if default else "")
                + ". The question stays open on the Decisions page.")
        self._run(["send", note, "--title", f"{self.ctx.routine.slug}: decision {self.qid}"])


def _reply_items(raw: str) -> list[tuple[int, str]]:
    """Parse `discord read --json` output — ONE pinned shape: a JSON list of message
    objects with a snowflake `id` and text in the `message` field (the util's _emit
    contract), returned as (id, text) ascending. A message without a parsable id is
    dropped — the F194 newer-than-question guard cannot order it. If the util's shape
    ever changes, change it here too — never re-grow tolerant multi-shape parsing.
    """
    try:
        data = json.loads(raw.strip() or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    items = []
    for item in data:
        if not isinstance(item, dict) or not str(item.get("message") or "").strip():
            continue
        try:
            mid = int(str(item.get("id")))
        except (TypeError, ValueError):
            continue
        items.append((mid, str(item["message"]).strip()))
    return sorted(items)


def mirror_blocking(ctx, qid: str, question: str, options: list[str], default: str,
                    timeout_min: int):
    """A live DiscordMirror for this question, or None when the routine is not set up
    for it (no communication permission / no discord util) or the channel is down.
    """
    g = ctx.grants
    if g is None or not notify.discord_enabled(ctx.server, granted_utils=g.utils):
        return None
    mirror = DiscordMirror(ctx, qid)
    return mirror if mirror.send_question(question, options, default, timeout_min) else None
