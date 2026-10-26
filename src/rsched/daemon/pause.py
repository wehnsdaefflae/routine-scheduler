"""Global scheduling pause (D34) — a durable sentinel the operator toggles from the UI.

While the sentinel exists the scheduler fires NOTHING on its own:

  * scheduled fires are SKIPPED — their next-fire time still advances normally, so
    resuming does not backlog-fire every routine that came due while paused. A skipped
    LANE fire also moves the lane's on-disk watermark (`rsched/lane_fires.py`), because
    that promise has to survive the next boot too: boot catch-up makes up a due fire
    nobody handled, and a deliberate skip is handled. What a skip DOES leave is a record
    that the fire is OWED — a lane's in `lane_fires`, a routine's in `routine_fires` — and
    lifting the pause makes up at most ONE fire per subject, and only while less than a
    quarter of that subject's own cadence has passed (D156 for lanes, D183 for routines:
    "skip, don't backlog" is right for a frequent cron and wrong for a twice-daily
    routine, which loses half a day per skip). Past that ratio the make-up would crowd
    the next fire, and the decline is itself an event;
  * trigger and one-shot intake is DEFERRED (their ticks don't run) — spooled webhook
    events and armed one-shots fire after resume; a one-shot is never consumed unfired;
  * a DETACHED background task is neither fired nor delivered-by-waking: the two phases of
    `daemon/detached.py` that START a run — intake's `runner.fire` and the wake's
    `resume_terminal` — are held, boot `reconcile()` included, while DELIVERY of a task that
    already finished proceeds (it copies artifacts and writes a durable inbox message, which
    starts nothing). Held, not dropped: the request file survives unconsumed and the wake is
    state-driven, so the first tick after the resume makes both good (F721);
  * the REAP's two automatic legs are held — the post-finish wake for a stranded message, and
    the boot re-open of a run a daemon RESTART killed (F746, `daemon/reap_resume.py`). Neither
    is a click or a schedule, and each starts an engine process: `runner.fire_blocker` does not
    test the pause, and `engine/control.pause_gate` holds only runs MARKED at the pause click
    (`web/settings/pause.py`), which a freshly resumed leg has no way to carry. Held, not
    dropped, on the same ground as the detached phases: the stranded message and the restart
    orphan's own note are durable, so the leg happens after the resume;
  * the reap's single SIGKILL RETRY (D99) is NOT held, and that is deliberate: it fires once,
    when the kernel has killed a run's process, and holding it would consume the one automatic
    recovery that run is owed (`tests/test_global_pause.py` states this in terms). It calls
    `holds_automatic_leg` nowhere. Until 2026-10-26 this list said it WAS held, which is the
    worst shape a contract can take: a reader auditing "does a paused instance start engines?"
    was told no by the document the file designates as the answer;
  * manual "run now" BYPASSES the pause on purpose: it is the operator's explicit
    override (option A of decision D34).

This list is the CONTRACT, so a new thing the daemon does on its own belongs in it or in the
exemption above. F721 is what the omission costs: `detached.tick` sat outside the scheduler's
pause guard for as long as the feature existed, and a paused instance went on starting runs with
no event, no log line and nothing on the Decisions page to read it from.

The flag survives daemon restarts (a file, exactly like the restart sentinel, in the
same dot-dir the registry scan ignores) and is reported in /api/status as `paused`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import ServerConfig


def sentinel_path(server: ServerConfig) -> Path:
    """Where the pause flag lives (sibling of the restart sentinel)."""
    return server.routines_home / ".control" / "pause.request"


def paused(server: ServerConfig) -> bool:
    return sentinel_path(server).exists()


def holds_automatic_leg(server: ServerConfig, what: str, who: str) -> bool:
    """True when a pause holds an AUTOMATIC run leg — and it logs why, once per attempt.

    The two callers are both `reap_resume`'s (`resume_for_stranded`, `resume_after_restart`).
    The sigkill retry is NOT one of them — see the module docstring. No health event is
    filed because nothing is DROPPED — the same
    argument `daemon/detached.py` makes for its two held phases — and DEBUG because the reap
    runs often enough that one standing fact would otherwise be hours of noise.
    """
    if not paused(server):
        return False
    logging.getLogger("rsched.daemon.pause").debug(
        "scheduling paused — %s held for %s; its state stays durable", what, who)
    return True


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
