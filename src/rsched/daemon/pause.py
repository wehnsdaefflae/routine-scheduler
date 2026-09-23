"""Global scheduling pause (D34) — a durable sentinel the operator toggles from the UI.

While the sentinel exists the scheduler fires NOTHING on its own:

  * scheduled fires are SKIPPED — their next-fire time still advances normally, so
    resuming does not backlog-fire every routine that came due while paused. A skipped
    LANE fire also moves the lane's on-disk watermark (`rsched/lane_fires.py`), because
    that promise has to survive the next boot too: boot catch-up makes up a due fire
    nobody handled, and a deliberate skip is handled;
  * trigger and one-shot intake is DEFERRED (their ticks don't run) — spooled webhook
    events and armed one-shots fire after resume; a one-shot is never consumed unfired;
  * manual "run now" BYPASSES the pause on purpose: it is the operator's explicit
    override (option A of decision D34).

The flag survives daemon restarts (a file, exactly like the restart sentinel, in the
same dot-dir the registry scan ignores) and is reported in /api/status as `paused`.
"""

from __future__ import annotations

from pathlib import Path

from ..config import ServerConfig


def sentinel_path(server: ServerConfig) -> Path:
    """Where the pause flag lives (sibling of the restart sentinel)."""
    return server.routines_home / ".control" / "pause.request"


def paused(server: ServerConfig) -> bool:
    return sentinel_path(server).exists()


def generation(server: ServerConfig) -> str:
    """Identify this durable pause cycle; an absent sentinel holds no run."""
    try:
        return sentinel_path(server).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def set_paused(server: ServerConfig, value: bool) -> None:
    """Preserve the current pause cycle on repeated pause requests."""
    p = sentinel_path(server)
    if value:
        p.parent.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4

        try:
            with p.open("x", encoding="utf-8") as handle:
                handle.write(uuid4().hex + "\n")
        except FileExistsError:
            pass
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
