"""Which shell commands may have WRITTEN, and the record that says one did (D158, R2654).

Every write protection the engine has lives in the FILE-action gate (`engine/fileops._write_gate`);
a `bash -c` redirect calls none of it. Inside the routine's OWN dir the kernel refuses those writes
anyway (`ownseal`, D179), so what remains ungated is every path outside it — the point of an
escape hatch. Where the jail cannot engage (`sandbox: off`, no Landlock) the marker below is again
the whole mechanism. Which writes a shell line performs is undecidable, so nothing here refuses.

D158 replaced prevention with ACCOUNTABILITY: a write-shaped command carries an `ungated_write`
marker on its observation AND files an `ungated_write` HEALTH EVENT, the durable stream an audit
reads. ONE ROW PER RUN, not per command: the audit needs WHICH RUN to open, and the per-call
detail is already in that run's transcript.

The marker is conservative in one direction only: a hit claims "this may have written", never
"this did not".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: Command shapes that may WRITE. Conservative by design (D158): a hit means "may have written,
#: and no file-action gate saw it". Flagging reads would make the marker noise, so the list
#: stays at the shapes that write in their ordinary use.
_WRITE_SHAPES = (
    r">",                                   # redirect + append (also catches >>)
    r"\b(?:tee|dd|truncate|install)\b",
    # A write COMMAND, only in command position — a bare `\bln\b` matched Python's `for ln in`
    # and narration's "touch", and the one-per-run latch then named a read. Command position =
    # the start, after a separator / line break, after a RUNNER (`sudo`, `time`, `env`,
    # `nohup`, `xargs`, `find -exec`) or a shell keyword, with the runner's own flags allowed
    # in between (`xargs -0 -r rm -f`). `tok=` is an assignment, never an invocation.
    # `-exec` has no leading `\b`: both ` ` and `-` are non-word, so `\b-exec` never matches
    # (F730; pinned by the `find-exec-rm` case).
    (r"(?:^|[\n;&|(`{]|\b(?:sudo|time|env|nohup|xargs|then|do|else)\s|-exec\s)"
     r"(?:\s*-{1,2}[\w-]+)*\s*"
     r"(?:cp|mv|rm|rmdir|mkdir|ln|touch|chmod|chown)(?:\s|$)(?!=)"),
    # A multi-token shape's scan stops at every SEPARATOR, `&` included: reading past `&&`
    # joins two commands into one that was never run.
    r"\bsed\b[^|;&]*\s-i",                  # in-place sed only; a filtering sed is a read
    # `git stash list|show` are read-only; every other verb here writes in every form.
    (r"\b(?:patch|git)\b[^|;&]*"
     r"\b(?:apply|checkout|reset|commit|add|restore"
     r"|stash(?!@)(?!\s+(?:list|show)\b))\b"),
    # An interpreter running an INLINE program (`-c`/`-e`, stdin `-`, or a heredoc) that
    # writes; the needle is a WRITE verb, not merely `open`, since `json.load(open(p))` reads.
    # `&` bounds the span up to the FLAG only — the program body after it may contain `&&`.
    (r"\b(?:python3?|perl|ruby|node)\b[^|;&]*?(?:(?<![\w-])-[ce]\b|(?<![\w-])-\s|<<).*?"
     r"(?:\b(?:write\w*|unlink|rmtree|makedirs|mkdir|rename|replace|dump)\b"
     r"|open\([^),]*,\s*['\"][^'\"]*[wax])"),
)
_WRITE_RE = re.compile("|".join(_WRITE_SHAPES), re.DOTALL)

#: A quoted span — the one place a `>` is TEXT rather than a redirect. Only the `>` inside it is
#: blanked, never the span's words: `python -c "open('x','w')"` must still flag on `open`.
_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")

#: `>=`, `->` and `=>` are COMPARISONS and arrows, never redirects — blanked before the shape
#: test (F730).
_COMPARISON = re.compile(r"->|=>|>=")


def marks_a_write(command: str) -> bool:
    """Whether this shell command's SHAPE suggests it wrote something (D158).

    Stream plumbing and text are neutralised before the test, or nearly every defensive command
    would flag:

    * `>/dev/null`, `2>/dev/null` and `2>&1` — stripped whole (the fd is optional).
    * `>=`, `->` and `=>` — blanked (comparisons in inline Python).
    * a `>` INSIDE a quoted string (`grep -n "a > b"`) — blanked in place. Only the `>`, never
      the span's words: `python3 -c "open('x','w')"` still flags on the write-mode open.

    NOT neutralised, deliberately: a bare `>` as "greater than" outside quotes still flags —
    stripping it would need a shell parser, and the marker errs toward flagging.
    """
    stripped = re.sub(r"\d?>&\d|\d?>\s*/dev/null", " ", command)
    stripped = _COMPARISON.sub(" ", stripped)
    stripped = _QUOTED.sub(lambda m: m.group(0).replace(">", " "), stripped)
    return bool(_WRITE_RE.search(stripped))


def already_filed(routines_home: Path, run_id: str) -> bool:
    """Whether this RUN already has its `ungated_write` row on the stream.

    The latch is read back out of the stream (memoized), not held in memory, so a resumed leg
    — a new process — does not file the row again. Fails OPEN: trouble reading the stream files
    the event rather than silencing it.
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
