"""The command-output spill store — `.util_outputs/`, the engine's copy of output too large
for the observation that carried it (`util`, `script`, `shell`, and any other kind that spills).

Output is captured up to `utils_lib.OUTPUT_CAP` and cut to `OBS_CAP_CHARS` for the observation;
the transcript records only the truncated text, and re-running is no recovery for anything
non-deterministic, paid or time-bound. This store keeps exactly the band between the two caps
and NOTHING else — an output the observation carried whole is already in the transcript.

Reads are ordinary `read_file`. Writes are engine-only (`fileops._write_gate`), like `runs/`: a
run must not rewrite its own evidence. The dir is gitignored on first use via
`paths.ensure_gitignored` (the autocommit is `git add -A` and util output can carry tokens). An
applied lossless compression (output_compression.py) keeps its original stdout under `runs/`
instead, out of this cache's pruning. Retention is KEEP_RUNS run dirs, pruned on write — a
backstop, never a promise.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import atomic_write, ensure_gitignored

if TYPE_CHECKING:
    from .run_context import RunContext

OUTPUTS_DIR = ".util_outputs"
KEEP_RUNS = 5


def _run_key(ctx: RunContext) -> str:
    """`<run-ts>` for a top-level run, `<run-ts>/sub-<n>` for a child. A child's turn
    numbering restarts, so without the suffix a subrun's turn 5 would overwrite its
    parent's.
    """
    try:
        rel = ctx.run_dir.relative_to(ctx.root_run_dir)
    except ValueError:
        return ctx.run_ts
    if digits := [p for p in rel.parts if p.isdigit()]:
        return f"{ctx.run_ts}/sub-{'-'.join(digits)}"
    return ctx.run_ts


def unused_name(path: Path) -> Path:
    """`path`, or the first free `<stem>-<n><suffix>` beside it. `t<turn>-<name>` is not unique
    within a run (a slash command runs under the previous model turn's number), and overwriting
    would leave an earlier observation pointing at output it never produced.
    """
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def _prune(base: Path) -> None:
    """Keep the KEEP_RUNS newest run dirs (ISO timestamps sort lexically); a run's
    sub-<n> dirs nest inside its own, so this prunes children with their parent.
    """
    runs = sorted(p for p in base.iterdir() if p.is_dir())
    for old in runs[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)


def spill(ctx: RunContext, name: str, out: str, err: str, *,
          out_truncated: bool, err_truncated: bool) -> dict | None:
    """Persist the full captured output of a util call whose observation was truncated.
    Returns the pointer the observation renders — {stdout, stdout_chars, stderr,
    stderr_chars} with relative paths — or None when nothing was written.

    Never raises: a failed spill must not fail the turn (the truncated observation still
    carries the head and tail).
    """
    if not (out_truncated or err_truncated):
        return None
    pointer: dict = {}
    try:
        base = ctx.routine.dir / OUTPUTS_DIR
        rel_dir = f"{OUTPUTS_DIR}/{_run_key(ctx)}"
        # git add -A would otherwise commit every spilled output into the routine's repo (and
        # a git sync push it), permanently, with whatever secrets a util printed
        ensure_gitignored(ctx.routine.dir, OUTPUTS_DIR,
                          "spilled util output (engine-owned, pruned)")
        (ctx.routine.dir / rel_dir).mkdir(parents=True, exist_ok=True)
        for stream, text, truncated in (("out", out, out_truncated),
                                        ("err", err, err_truncated)):
            if not truncated:
                continue
            target = unused_name(ctx.routine.dir / rel_dir / f"t{ctx.turn}-{name}.{stream}")
            atomic_write(target, text)
            key = "stdout" if stream == "out" else "stderr"
            pointer[key] = str(target.relative_to(ctx.routine.dir))
            pointer[f"{key}_chars"] = len(text)
            if getattr(text, "capture_truncated", False):
                pointer[f"{key}_capture_truncated"] = True
        _prune(base)
    except (OSError, ValueError, AttributeError):
        # best-effort, like the note channel: a degenerate path raises before the OS is
        # even reached, and either way the turn must survive it
        return pointer or None
    return pointer or None


def pointer_line(pointer: dict) -> str:
    """The observation's `[full output]` line — the pointer at the moment of need, which
    is why the store needs no index: a run never has to guess a filename.
    """
    saved = [
        (f"the incomplete capture envelope ({pointer[k + '_chars']} chars) "
         f"for {k} at `{pointer[k]}`"
         if pointer.get(k + "_capture_truncated")
         else f"the complete {pointer[k + '_chars']}-char {k} at `{pointer[k]}`")
        for k in ("stdout", "stderr") if pointer.get(k)]
    # Not "the elided middle": stdout keeps its HEAD (observations.truncate), stderr loses its
    # middle, and an applied compression loses nothing.
    return (" and ".join(saved).capitalize()
            + " — read_file it (start_line/max_lines page it) for what the preview leaves "
              "out, instead of re-running the util.")


#: The kinds whose own body already renders the pointer (`observations._run_body`).
_SPILLS_IN_ITS_BODY = frozenset({"util", "script", "shell"})


def observation_tail(obs: dict) -> str:
    """The `[full output]` tail for a kind whose body does NOT render it itself (D246).

    `util`, `script` and `shell` emit it inside their shared body and are excluded, so nothing
    doubles; every OTHER kind that spills gets the pointer at the one place a rendered
    observation is assembled, rather than per-kind wiring a new renderer could miss.
    """
    pointer = obs.get("full_output")
    if not pointer or obs.get("kind") in _SPILLS_IN_ITS_BODY:
        return ""
    return "\n[full output] " + pointer_line(pointer)


def digest(routine_dir: Path, limit: int = 8) -> str:
    """The state-digest line: the newest spilled outputs from EARLIER runs, so a run can
    read what a previous one fetched instead of fetching it again. Empty when the store
    is (the common case — nothing is added to the prompt until something spills).
    """
    base = routine_dir / OUTPUTS_DIR
    if not base.is_dir():
        return ""
    # Newest by when it was WRITTEN: a path sort puts `t9-…` above `t10-…`.
    try:
        files = sorted(((p.stat(), p) for p in base.rglob("*") if p.is_file()),
                       key=lambda sp: (sp[0].st_mtime_ns, str(sp[1])), reverse=True)
    except OSError:
        return ""
    if not files:
        return ""
    rows = [f"- {p.relative_to(routine_dir)} ({st.st_size}B)" for st, p in files[:limit]]
    more = (f"\n[... {len(files) - limit} older ones — the last {KEEP_RUNS} runs are kept]"
            if len(files) > limit else "")
    return (f"{OUTPUTS_DIR}/ (util output too large for the observation that carried it, saved "
            "in full — read_file one when you need what an earlier call already fetched, "
            "rather than re-running the util):\n" + "\n".join(rows) + more)
