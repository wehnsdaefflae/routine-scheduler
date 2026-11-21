"""The note channel — engine-side capture of the per-action `note` field.

A note is 1-3 SELF-CONTAINED lines the model marks as worth keeping beyond the context window.
It rides ANY action at no turn cost, so insight is not priced at a dedicated turn.

The engine appends each note to state/notes.md with a provenance stamp (run · turn · phase ·
action). The stamp is an ADDRESS into the transcript; self-containment is the WRITER's job.

notes.md stays ordinary run-writable state (the transcript keeps the raw record). It is not
LEDGER.md (`rsched/runledger.py`), and curation into `.memory/` stays a deliberate
memory_write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import digesttail

if TYPE_CHECKING:
    from .run_context import RunContext

NOTE_MAX_CHARS = 500
NOTES_FILE = "notes.md"
_HEADER = ("# Notes — findings captured via the `note` field (engine-appended, stamped; "
           "prunable — see the transcript for each note's full context)\n\n")


def _brief(action: dict) -> str:
    """Kind plus its most identifying field — enough to locate the moment, not describe it.

    The canonical rendering (`actionschema.canon`), truncated.
    """
    from .actionschema import canon

    return canon(action)[:80]


def capture(ctx: RunContext, action: dict) -> None:
    """Append the action's note (if any) to state/notes.md, stamped. Never raises — a
    failed capture must not fail the turn (the note is still on the transcript event).
    """
    note = " ".join(str(action.get("note") or "").split())
    if not note:
        return
    if len(note) > NOTE_MAX_CHARS:
        note = note[:NOTE_MAX_CHARS] + " …[truncated]"
    append_line(ctx, note, source=_brief(action))


def append_line(ctx, text: str, *, source: str) -> None:
    """Append ONE stamped line to `state/notes.md` — the channel the next run's digest reads.

    Shared by both writers — `capture` and a `run-end` hook (`engine/assist.py`) — so the file
    keeps ONE shape the tail can parse. Never raises.
    """
    stamp = f"[{ctx.run_ts} · turn {ctx.turn} · {getattr(ctx, 'phase', None) or '—'} · {source}]"
    from .run_context import work_dir

    # an open TASK's notes go to its own workspace (engine/jobops.py), where its next
    # briefing reads their tail — the stamp's run id still addresses this run's transcript
    path = work_dir(ctx) / "state" / NOTES_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            if fh.tell() == 0:
                fh.write(_HEADER)
            fh.write(f"- {stamp} {text}\n")
    except (OSError, ValueError):
        # best-effort: a degenerate path raises ValueError before it ever reaches the
        # OS — either way the turn must not fail; the transcript already has the note
        pass


def tail(routine_dir, lines: int = 10) -> str:
    """The last N note lines for the state digest — notes reach the next run without a
    read, while the full file stays on-demand (keep the prompt lean).
    """
    path = routine_dir / "state" / NOTES_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    noted = [ln for ln in text.splitlines() if ln.startswith("- ")]
    # Newest last, bounded in CHARACTERS (engine/digesttail.py); the OLDEST kept lines go
    # first when the budget bites — the whole file is one read away.
    newest_last = "\n".join(noted[-lines:])
    if len(newest_last) <= digesttail.TAIL_BUDGET:
        return newest_last
    kept: list[str] = []
    size = 0
    for line in reversed(noted[-lines:]):
        if kept and size + len(line) + 1 > digesttail.TAIL_BUDGET:
            break
        kept.append(line)
        size += len(line) + 1
    dropped = len(newest_last) - size
    return ("\n".join(reversed(kept))
            + f"\n[... {dropped:,} more characters of older notes clipped — "
              f"state/{NOTES_FILE} holds them whole]")
