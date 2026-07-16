"""The note channel — engine-side capture of the per-action `note` field.

A note is 1-3 SELF-CONTAINED lines the model marks as worth keeping beyond the context
window (a confirmed finding, a dead end, a fallback plan, an unresolved doubt). It rides
ANY action at no turn cost — the one-action-per-turn contract prices every dedicated
write at a full turn, which is why insights historically died with the window (models
defer bookkeeping under budget pressure, and end-of-run writes are reconstructions).

The engine appends each note to the routine's state/notes.md with a provenance stamp
(run · turn · phase · action). The stamp is an ADDRESS: the note's full context lives in
the transcript / the compaction history archive, reachable by run+turn — notes carry the
address of their context, not the context itself; the semantic half of context is the
WRITER'S job (the contract demands self-containment, like a subrun brief or a finish
summary — every boundary-crossing artifact in this system).

notes.md stays ordinary run-writable state: the model may reorganize it, the improver
prunes it (a note that can't be understood alone is broken). The transcript keeps the
raw record on the action event regardless — this file is the working copy, exactly the
LEDGER's relationship to the transcript. Curation into the indexed cross-run store stays
a deliberate memory_write (that turn price is the memory INDEX's quality gate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .run_context import RunContext

NOTE_MAX_CHARS = 500
NOTES_FILE = "notes.md"
_HEADER = ("# Notes — findings captured via the `note` field (engine-appended, stamped; "
           "prunable — see the transcript for each note's full context)\n\n")


def _brief(action: dict) -> str:
    """Kind plus its most identifying field — enough to locate the moment, not describe it."""
    kind = str(action.get("kind") or "?")
    target = action.get("name") or action.get("path") or ""
    if not target and isinstance(action.get("paths"), list) and action["paths"]:
        target = action["paths"][0]
    target = str(target)[:60]
    return f"{kind} {target}".strip()


def capture(ctx: RunContext, action: dict) -> None:
    """Append the action's note (if any) to state/notes.md, stamped. Never raises — a
    failed capture must not fail the turn (the note is still on the transcript event).
    """
    note = " ".join(str(action.get("note") or "").split())
    if not note:
        return
    if len(note) > NOTE_MAX_CHARS:
        note = note[:NOTE_MAX_CHARS] + " …[truncated]"
    stamp = f"[{ctx.run_ts} · turn {ctx.turn} · {ctx.phase or '—'} · {_brief(action)}]"
    path = ctx.routine.dir / "state" / NOTES_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            if fh.tell() == 0:
                fh.write(_HEADER)
            fh.write(f"- {stamp} {note}\n")
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
    return "\n".join(noted[-lines:])
