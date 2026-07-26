"""The util-output spill store — `.util_outputs/`, the engine's copy of output too large
for the observation that carried it.

A util's stdout is captured up to `utils_lib.OUTPUT_CAP` (1 MB) and then head+tail
truncated to `OBS_CAP_CHARS` for the observation. The transcript records the TRUNCATED
observation, so everything between those two caps was produced and immediately destroyed:
the only recovery was re-running the util, which does not return the same data for
anything non-deterministic, paid, or time-bound (a page fetch, an LLM subcall, a mailbox
read, a quote). This store keeps exactly that band.

It keeps NOTHING else. An output the observation carried whole is already in the
transcript verbatim, and a second copy here would duplicate a file the system has. The
store is therefore the recovery of a loss, not a mirror of util traffic.

Reads are ordinary `read_file`, which pages by line window — a large output is cheaper to
consult on disk than it ever was in context, where it only existed as a head+tail guess.
Writes are engine-only (`fileops._write_gate`), like `runs/`: a run must not be able to
rewrite its own evidence. The dir is gitignored on first use — the run-end autocommit is
`git add -A` and util output can carry tokens — mirroring `machines._ensure_mnt_gitignored`.

Retention is KEEP_RUNS run directories, pruned on write: a backstop against unbounded
growth, never a promise about how long an output survives.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import atomic_write

if TYPE_CHECKING:
    from .run_context import RunContext

OUTPUTS_DIR = ".util_outputs"
KEEP_RUNS = 5


def _ensure_ignored(routine_dir: Path) -> None:
    """Keep the store out of the engine autocommit — `git add -A` would otherwise commit
    every spilled output into the routine's repo (and `git-sync` push it to a remote),
    permanently, including whatever secrets a util printed.
    """
    gi = routine_dir / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if any(ln.strip().rstrip("/") == OUTPUTS_DIR for ln in lines):
        return
    atomic_write(gi, "\n".join([*lines, "# spilled util output (engine-owned, pruned)",
                                f"{OUTPUTS_DIR}/", ""]))


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
    base = ctx.routine.dir / OUTPUTS_DIR
    rel_dir = f"{OUTPUTS_DIR}/{_run_key(ctx)}"
    pointer: dict = {}
    try:
        _ensure_ignored(ctx.routine.dir)
        (ctx.routine.dir / rel_dir).mkdir(parents=True, exist_ok=True)
        for stream, text, truncated in (("out", out, out_truncated),
                                        ("err", err, err_truncated)):
            if not truncated:
                continue
            rel = f"{rel_dir}/t{ctx.turn}-{name}.{stream}"
            atomic_write(ctx.routine.dir / rel, text)
            key = "stdout" if stream == "out" else "stderr"
            pointer[key] = rel
            pointer[f"{key}_chars"] = len(text)
        _prune(base)
    except (OSError, ValueError):
        # best-effort, like the note channel: a degenerate path raises before the OS is
        # even reached, and either way the turn must survive it
        return pointer or None
    return pointer or None


def pointer_line(pointer: dict) -> str:
    """The observation's `[full output]` line — the pointer at the moment of need, which
    is why the store needs no index: a run never has to guess a filename.
    """
    saved = [f"the complete {pointer[k + '_chars']}-char {k} at `{pointer[k]}`"
             for k in ("stdout", "stderr") if pointer.get(k)]
    return (" and ".join(saved).capitalize()
            + " — read_file it (start_line/max_lines page it) for the elided middle "
              "instead of re-running the util.")


def digest(routine_dir: Path, limit: int = 8) -> str:
    """The state-digest line: the newest spilled outputs from EARLIER runs, so a run can
    read what a previous one fetched instead of fetching it again. Empty when the store
    is (the common case — nothing is added to the prompt until something spills).
    """
    base = routine_dir / OUTPUTS_DIR
    if not base.is_dir():
        return ""
    try:
        files = sorted((p for p in base.rglob("*") if p.is_file()), reverse=True)
    except OSError:
        return ""
    if not files:
        return ""
    rows = [f"- {p.relative_to(routine_dir)} ({p.stat().st_size}B)" for p in files[:limit]]
    more = (f"\n[... {len(files) - limit} older ones — the last {KEEP_RUNS} runs are kept]"
            if len(files) > limit else "")
    return (f"{OUTPUTS_DIR}/ (util output too large for the observation that carried it, saved "
            "in full — read_file one when you need what an earlier call already fetched, "
            "rather than re-running the util):\n" + "\n".join(rows) + more)
