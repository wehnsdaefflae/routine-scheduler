"""Which shell commands may have WRITTEN, and the record that says one did (D158, R2654).

Every write protection the engine has lives in the FILE-action gate (`engine/fileops._write_gate`):
the `.memory/` seal, the engine-ownership of `runs/`, `.util_outputs/` and `.doc_cache/`, the
`routine.yaml` config refusal, the recipe-authoring boundary and `_removal_gate`. A `bash -c`
redirect calls none of them, so all of them are bypassable by any routine holding `shell` — which
is the POINT of an escape hatch, and is why the capability is granted sparingly rather than gated
per path. Deciding WHICH writes a shell line performs is undecidable in general (a command can
write through a script, a heredoc, an editor, a compiler), so nothing here tries to refuse one.

D158 replaced prevention with ACCOUNTABILITY, and R2654 is the half that was never connected:
the observation carried an `ungated_write` marker and the whole product had no reader for it — two
lines in `src/rsched`, both the writer, no read model, no endpoint, no console case. A compensating
control nothing reads is not one. So a write-shaped command now also files a `ungated_write`
HEALTH EVENT, which puts the run in the one durable, machine-checked stream an audit reads
(`.control/health-events.jsonl`) instead of only in the transcript nobody greps.

ONE ROW PER RUN, not per command. A run that works in a repo makes dozens of write-shaped calls,
and `health_cooldown`'s docstring records what per-occurrence filing did to this stream once: one
resumed run wrote 26 rows of 1,289 for a condition 80 runs had produced, and an operator reading
"173 cost events" saw a fleet problem that was not there. What the audit needs from this event is
WHICH RUN to open — the per-call detail is already in that run's transcript, on every observation
the marker rode. So the first write-shaped command of a run files the row, naming the turn and the
command, and the rest of that run's writes are found by reading it.

The marker is deliberately conservative in one direction only: a hit claims "this may have
written", never "this did not".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: Command shapes that may WRITE. Conservative by design (D158): a hit means "this command may
#: have written, and no file-action gate saw it", never "this command did write". Missing a write
#: costs an audit trail entry; flagging a read would make the marker noise, so the list stays at
#: the shapes that write in their ordinary use.
_WRITE_SHAPES = (
    r">",                                   # redirect + append (also catches >>)
    r"\b(?:tee|dd|truncate|install)\b",
    r"\b(?:cp|mv|rm|rmdir|mkdir|ln|touch|chmod|chown)\b",
    r"\bsed\b[^|;]*\s-i",                   # in-place sed only; a filtering sed is a read
    r"\b(?:patch|git)\b[^|;]*\b(?:apply|checkout|reset|commit|add|restore|stash)\b",
    # An interpreter running an INLINE program that writes. The program arrives three ways —
    # `-c`/`-e`, stdin (`python3 -`) or a heredoc — and a routine's state edits use all three,
    # so all three are matched; the needle is then a WRITE verb, not merely `open`, because
    # `json.load(open(p))` is how every routine READS its own state.
    (r"\b(?:python3?|perl|ruby|node)\b[^|;]*?(?:(?<![\w-])-[ce]\b|(?<![\w-])-\s|<<).*?"
     r"(?:\b(?:write\w*|unlink|rmtree|makedirs|mkdir|rename|replace|dump)\b"
     r"|open\([^),]*,\s*['\"][^'\"]*[wax])"),
)
_WRITE_RE = re.compile("|".join(_WRITE_SHAPES), re.DOTALL)

#: A quoted span — the one place a `>` is TEXT rather than a redirect. Only the `>` inside it is
#: blanked, never the span's words: `python -c "open('x','w')"` must still flag on `open`.
_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")

#: `>=`, `->` and `=>` are COMPARISONS and arrows, never redirects. Inline Python is how a routine
#: edits its own state, and `if ts >= '2026-10-19'` is its commonest line — blanked before the
#: shape test so a date filter does not read as a write (F730).
_COMPARISON = re.compile(r"->|=>|>=")


def marks_a_write(command: str) -> bool:
    """Whether this shell command's SHAPE suggests it wrote something (D158).

    Two things are stream plumbing or text rather than a write to a file the run owns, and both
    are neutralised before the test — otherwise nearly every defensive command would flag and the
    marker would stop being worth reading:

    * `>/dev/null`, `2>/dev/null` and `2>&1` — stripped whole. The fd is optional: discarding
      STDOUT (`ls "$d" >/dev/null && echo ok`) is a probe idiom, not a write.
    * `>=`, `->` and `=>` — blanked. Inline Python is how a routine edits its own state and
      `if ts >= '2026-10-19'` is its commonest line; read as a redirect it made the marker fire
      on pure reads.
    * a `>` INSIDE a quoted string (`grep -n "a > b"`, `echo 'x > y'`) — blanked in place. This
      is what the first three `ungated_write` health events caught: two of them were read-only
      commands whose grep pattern or echo text contained a `>`, so the stream's very first signal
      was 1-in-3 false. A new detector a reader learns to discount is worse than none. Only the
      `>` is blanked, never the quoted span's words: a redirect outside the quotes still flags
      (`echo hi > "state/x"`), and `python3 -c "open('x','w')"` flags on the write-mode open.

    What is NOT neutralised, deliberately: a bare `>` used as "greater than" OUTSIDE a quoted
    span — a multi-line `-c` program comparing with `>` rather than `>=` still flags. Widening
    the strip that far would need to parse the shell line, and the marker's one promise is to be
    conservative in the FLAGGING direction.
    """
    stripped = re.sub(r"\d?>&\d|\d?>\s*/dev/null", " ", command)
    stripped = _COMPARISON.sub(" ", stripped)
    stripped = _QUOTED.sub(lambda m: m.group(0).replace(">", " "), stripped)
    return bool(_WRITE_RE.search(stripped))


def already_filed(routines_home: Path, run_id: str) -> bool:
    """Whether this RUN already has its `ungated_write` row on the stream.

    The latch is read back out of the stream rather than held in memory — `health_cooldown`'s
    idiom, and for its reason: a writer inside one turn has no long-lived object to latch on,
    and a resumed leg is a new process that must not file the row again. The parse is the
    stream's ONE memoized reader, so a run making dozens of write-shaped calls costs one read.

    Fails OPEN: trouble reading the stream files the event. A suppressor that cannot read its
    own history must not become the thing that silences the log.
    """
    if not run_id:
        return False
    try:
        from ..readmodels.health_stream import health_records

        return any(rec.get("event") == "ungated_write" and rec.get("run_id") == run_id
                   for rec in reversed(health_records(Path(routines_home))))
    except Exception:
        return False


def record_ungated_write(ctx: RunContext, command: str) -> None:
    """File the run's `ungated_write` health event, once per run (R2654).

    Best-effort: `log_health_event` swallows its own I/O errors, so a stream that cannot be
    written costs an audit row and never a turn.
    """
    home = Path(ctx.server.routines_home)
    run_id = str(getattr(ctx, "run_id", "") or "")
    if already_filed(home, run_id):
        return
    from ..health_events import log_health_event

    log_health_event(
        home, "ungated_write",
        routine=getattr(ctx.routine, "slug", "") or "", run_id=run_id,
        detail=f"turn {getattr(ctx, 'turn', 0)}: shell command whose shape may have written, "
               f"seen by no file-action gate — {command[:200]}",
        turn=getattr(ctx, "turn", 0) or None)
