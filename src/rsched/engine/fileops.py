"""File-shaped effect handlers: read_file / view_image / write_file / edit_file, the
memory actions, and read_rule — plus the path gates they share (the runs/ read depth,
the write-grounding rule, the recipe/config seal). Split from executor.py, which keeps
dispatch, the util runner, and the llm subcall (and routes the file kinds here).
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import atomic_write, resolve_rel
from ..readmodels.statemap import STAGES_DIR
from . import fileformat
from .observations import OBS_CAP_CHARS
from .outputs import OUTPUTS_DIR
from .run_context import RunContext

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

READ_DEFAULT_MAX_LINES = 200
READ_WINDOW_MAX_LINES = 500          # the schema's `max_lines` maximum, mirrored
# The largest file read_file will OPEN. The read used to materialise the whole file before
# windowing it — `read_text(errors="replace")` on a 1.5 GB .mkv became a multi-GB str plus a
# second copy from splitlines() — and on 2026-09-14 that swap-thrashed the 3.4 GB host for
# five hours until a physical reset (tv-show-tracker-seedbox-manager, reading a media file to
# satisfy the read-before-delete gate). 8 MiB is ~1 000 windows of the observation cap; a
# larger text file is paged with shell (head / sed -n) or a util, which stream.
READ_MAX_BYTES = 8 * 1024 * 1024
BINARY_SNIFF_BYTES = 8 * 1024        # a NUL byte in the first 8 KiB marks a file binary
UTIL_DEFAULT_TIMEOUT_S = 300
VISION_UTIL = "vision"
VIEW_DEFAULT_PROMPT = ("Describe this file in full detail — transcribe any text verbatim and "
                       "note structure, data, and notable visual elements.")


def _runs_read_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for previous-run access (grants.deny handles the relative-path form inside
    the schema-retry cycle; this catches absolute paths and scopes `runs: last`). The
    current run's own tree — status, archived history — is always readable.
    """
    g = ctx.grants
    if g is None:
        return None
    runs_dir = ctx.routine.dir / "runs"
    try:
        rel = resolved.relative_to(runs_dir)
    except ValueError:
        return None
    if resolved.is_relative_to(ctx.root_run_dir):
        return None
    if g.run_history == "none":
        # after D96 a routine's own policy always carries at least "last" — this branch
        # serves scopes genuinely without history (sub-workflow children, synthetic
        # policies), so the copy must not claim the ROUTINE lacks a permission (R46).
        return ("previous runs are not readable in this scope — a routine reads its own "
                "last run by default, but sub-workflows run without run history; fold "
                "what the child needs into its brief instead")
    if g.run_history == "last":
        prior = sorted(d.name for d in runs_dir.iterdir()
                       if d.is_dir() and d.name != ctx.root_run_dir.name)
        last = prior[-1] if prior else None
        if not rel.parts or rel.parts[0] != last:
            return (f"previous runs are readable at the default 'last' depth only "
                    f"({'runs/' + last if last else 'none exists yet'}); the run-history "
                    f"permission raises the depth to 'all' for longitudinal work")
    return None


def _window(lines: Iterable[str], start: int, max_lines: int) -> tuple[list[str], int]:
    """The [start, start + max_lines) slice of a line STREAM plus the stream's length. The
    stream is consumed to its end for the count but never held whole — one line in memory
    at a time, and the file is already under READ_MAX_BYTES, so the count is cheap.
    """
    window: list[str] = []
    stop = start + max_lines
    total = 0
    for total, line in enumerate(lines, 1):
        if start <= total < stop:
            window.append(line)
    return window, total


def _listing_lines(path: Path) -> Iterator[str]:
    """A directory's content IS its entries: one line each — `dir` / `file` / `link`, a
    file's size in bytes, the name — sorted by name, so a listing pages like a file. This is
    how a season pack of 1.5 GB media files is LOOKED AT before it is deleted: by name and
    size, never decoded.
    """
    with os.scandir(path) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        if e.is_symlink():
            yield f"link {'-':>12}  {e.name} -> {Path(e.path).readlink()}"
        elif e.is_dir(follow_symlinks=False):
            yield f"dir  {'-':>12}  {e.name}/"
        else:
            try:
                size: int | str = e.stat(follow_symlinks=False).st_size
            except OSError:
                size = "?"
            yield f"file {size:>12}  {e.name}"


def _refusal(rel_path: str, path: Path) -> dict | None:
    """The two reads that must never happen, decided from a stat and an 8 KiB sniff BEFORE
    anything is decoded: a binary file (a NUL byte in its head) and a file over
    READ_MAX_BYTES. Both carry `size` — the whole of what read_file can tell about such a
    file, and the reason the refusal still counts as having LOOKED at it for the
    destruction gate (history.seen_paths reads the key back on resume).
    """
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(BINARY_SNIFF_BYTES)
    if b"\0" in head:
        return {"path": rel_path, "size": size,
                "error": f"binary file ({size:,} bytes) — read_file shows text only; "
                         "view_image sees an image or PDF, a util or shell handles the rest"}
    if size > READ_MAX_BYTES:
        return {"path": rel_path, "size": size,
                "error": f"{size:,} bytes exceeds the read_file cap of {READ_MAX_BYTES:,} "
                         "bytes — page it with shell (head / sed -n) or a util instead"}
    return None


def _windowed(rel_path: str, window: list[str], total: int, start: int,
              max_lines: int) -> dict:
    end_line = min(start - 1 + max_lines, total)
    content = "\n".join(window)
    truncated = False
    if len(content) > OBS_CAP_CHARS:
        # END-truncate a file read: keep whole lines from the HEAD, drop the tail, and report
        # the exact next start_line — so a follow-up read continues IN SEQUENCE. (Opaque output
        # rides truncate()'s head+tail elision, where the tail's tail — a traceback end — must
        # survive; a file read is ordered, so dropping the tail and resuming is the right shape.
        # Operator AUDIT note, F204.)
        kept: list[str] = []
        used = 0
        for ln in window:
            if kept and used + len(ln) + 1 > OBS_CAP_CHARS:
                break
            kept.append(ln)
            used += len(ln) + 1
        end_line = start + len(kept) - 1
        core = "\n".join(kept)
        if len(core) > OBS_CAP_CHARS:          # a single line longer than the cap
            core = core[:OBS_CAP_CHARS]
        content = (core + f"\n[... {len(kept)} of {len(window)} window lines shown (through line "
                   f"{end_line} of {total}); truncated at {OBS_CAP_CHARS} chars — re-read "
                   f"with start_line={end_line + 1} to continue in sequence ...]")
        truncated = True
    return {"path": rel_path, "start_line": start,
            "end_line": end_line, "total_lines": total,
            "content": content, "truncated": truncated}


def _read_one(rel_path: str, action: dict, ctx: RunContext) -> dict:
    start = max(1, int(action.get("start_line") or 1))
    max_lines = min(int(action.get("max_lines") or READ_DEFAULT_MAX_LINES),
                    READ_WINDOW_MAX_LINES)
    directory = False
    try:
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.read_roots())
        if err := _memory_gate(ctx, path) or _runs_read_gate(ctx, path):
            return {"path": rel_path, "error": err}
        if path.is_dir():
            directory = True
            window, total = _window(_listing_lines(path), start, max_lines)
        else:
            if refusal := _refusal(rel_path, path):
                ctx.seen_paths.add(str(path))   # a stat IS a look: grounds delete/move/overwrite
                return refusal
            # streamed, never materialised: only the window is ever held in memory
            with path.open(encoding="utf-8", errors="replace") as fh:
                window, total = _window((line.rstrip("\n") for line in fh), start, max_lines)
    except (OSError, PermissionError) as exc:
        return {"path": rel_path, "error": str(exc)}
    ctx.seen_paths.add(str(path))
    # Reading a stage module IS the run's state transition — every recipe routes by
    # "read the module for where you are" — so the engine tracks the live phase right
    # here (→ status.json → the SSE state event) with zero recipe cooperation; the
    # stage modules are the state graph's nodes (statemap), so the names always match.
    if (path.suffix == ".md" and path.parent.name == STAGES_DIR
            and path.parent.parent == ctx.routine.dir):
        ctx.phase = path.stem
        # …and the PATH taken, not just the current node (F521/R1681): a run that skipped
        # a declared stage was indistinguishable from one that worked through all of them.
        if path.stem not in ctx.phases_entered:
            ctx.phases_entered.append(path.stem)
    obs = _windowed(rel_path, window, total, start, max_lines)
    if directory:
        obs["directory"] = True
    return obs

def _note_recorded_phase(ctx: RunContext, path) -> None:
    """Record a phase the run WROTE for itself — the second half of stage coverage (F563).

    The read stamp below sees a stage only when the run RE-READS `stages/<name>.md`, so it
    measures module re-reads rather than stage work: a routine whose recipe the model already
    holds routes by writing `state/phase.json` and read as one that skipped everything (24 of
    73 fleet runs on 2026-09-26; `llmsectest-weekday` 0 of 7 entered across three runs of
    404-423 turns). A phase the run wrote down is the stronger claim of the two, and it is
    already on disk — this only notices it.

    Deliberately quiet: a cursor that is missing, malformed or not a stage name is simply not
    evidence, and a write must never fail because of what it happened to contain. Validating
    the name against the declared set is `stage_coverage`'s job, not this seam's.
    """
    if path.name != "phase.json" or path.parent != ctx.routine.dir / "state":
        return
    try:
        phase = json.loads(path.read_text(encoding="utf-8")).get("phase")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(phase, str) and phase and phase not in ctx.phases_recorded:
        ctx.phases_recorded.append(phase)


def do_read_file(action: dict, ctx: RunContext) -> dict:
    paths = action.get("paths")
    if paths:  # batched read: several files in ONE action, one entry each
        return {"kind": "read_file", "files": [_read_one(str(p), action, ctx) for p in paths]}
    return {"kind": "read_file", **_read_one(action["path"], action, ctx)}


MEMORY_REFUSAL = (".memory/ is reachable only through memory_read / memory_write — the engine "
                  "owns .memory/INDEX.md (built from each note's 'about' line) and enforces "
                  "the note cap there, and both are bypassed by a generic file action")


def _memory_gate(ctx: RunContext, resolved) -> str | None:
    """The `.memory/` seal, on the RESOLVED path — where its siblings live.

    `engine/actions.py` also tests it, but LEXICALLY, on the string the model supplied: it is
    the cheap schema-retry correction that costs no turn. It is not a seal:
    `state/../.memory/INDEX.md` and the absolute form both walk straight past it and
    `resolve_rel` puts them right back inside the own dir. The engine-owned index, the
    100-line note cap and the `memory` capability gate all went with them.
    """
    return MEMORY_REFUSAL if resolved.is_relative_to(ctx.routine.dir / ".memory") else None


def _write_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for engine-owned and permission-gated writes (grants.deny handles the
    relative-path form; this catches absolute paths into the routine's own dir).
    """
    if err := _memory_gate(ctx, resolved):
        return err          # structural, not a grant: it holds with no policy loaded
    g = ctx.grants
    if g is None:
        return None
    if resolved.is_relative_to(ctx.routine.dir / "runs"):
        return "runs/ is engine-owned and read-only for the run"
    if resolved.is_relative_to(ctx.routine.dir / OUTPUTS_DIR):
        return (f"{OUTPUTS_DIR}/ is engine-owned and read-only for the run — it is the saved "
                "full text of util output too large for its observation (read_file it); a run "
                "does not rewrite the record of what a util returned")
    # routine.yaml is config — never writable by ANY run (even the improver, even when the
    # recipe is unlocked): config is the user's, changed via the UI or a deferred ask_user.
    # Machine-tunable behavior knobs (deliberation) live in tuning.yaml, which is RECIPE.
    if resolved.name == "routine.yaml":
        return ("routine.yaml is config (permissions, capabilities, budgets, roots) — no run "
                "edits it, not even the routine-improver (machine-tunable knobs live in "
                "tuning.yaml); file a deferred ask_user instead")
    if not g.recipe_unlocked:
        from ..grantpolicy import is_recipe_path

        try:
            rel = resolved.relative_to(ctx.routine.dir)
        except ValueError:
            return None
        if is_recipe_path(str(rel)):
            return ("editing this routine's own recipe (main.md / stages/ / tuning.yaml) "
                    "needs the recipe-authoring permission, which this routine does not "
                    "hold — its instructions are the user's. File a deferred ask_user (or a "
                    "report) describing the change instead")
    return None


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "write_file", "path": action["path"], "error": err}
        # Grounding gate: write_file REPLACES a file wholesale. Overwriting one OUTSIDE
        # the routine's own dir (a project file under an fs_write_root) requires having
        # seen it this run — a model that never read the content cannot know what it
        # destroys. The own dir is exempt (state/report rewrites are its normal mode);
        # append adds without destroying; creating a new file needs no grounding.
        if (path.is_file() and not action.get("append")
                and not path.is_relative_to(ctx.routine.dir)
                and str(path) not in ctx.seen_paths):
            return {"kind": "write_file", "path": action["path"],
                    "error": "this OVERWRITES an existing file this run has never read — "
                             "read_file it first (then overwrite knowingly), or use "
                             "edit_file with a verbatim anchor for a targeted change"}
        path.parent.mkdir(parents=True, exist_ok=True)
        data = action["content"]
        if not isinstance(data, str):
            # Structured content arrives as a live JSON value — models need not escape
            # file bodies into strings; we serialize. An APPENDED value is a RECORD, not a
            # document: one compact line, so a JSONL ledger stays one-object-per-line. The
            # pretty form landed a 13-line row in self-audit's changelog.jsonl, which every
            # reader of that file then counted as 13 malformed rows.
            data = (json.dumps(data, ensure_ascii=False) if action.get("append")
                    else json.dumps(data, indent=2, ensure_ascii=False)) + "\n"
        if action.get("append"):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(data)
        else:
            # F460, the write_file half: only a STR body over an existing file can break a
            # format the engine did not produce (structured content is serialized here, and
            # an append is not one document).
            if (isinstance(action["content"], str) and path.is_file()
                    and (err := fileformat.check_after(
                        path, path.read_text(encoding="utf-8", errors="replace"), data))):
                return {"kind": "write_file", "path": action["path"], "error": err}
            # Atomic (tmp+rename): another process reading this path — self-audit reading any
            # routine, or the target routine's own run when the improver rewrites its recipe
            # under an fs_write_root — sees the old or new file whole, never a torn write. Its
            # git autocommit / pre-run recipe snapshot can't stage a half-written file either.
            keep = path.stat().st_mode & 0o7777 if path.exists() else None
            atomic_write(path, data, mode=keep)
        ctx.seen_paths.add(str(path))   # written = seen: a rewrite of own output is grounded
        _note_recorded_phase(ctx, path)   # the run's own cursor is stage-coverage evidence (F563)
        size = path.stat().st_size      # TOTAL bytes on disk after the write
    except (OSError, PermissionError) as exc:
        return {"kind": "write_file", "path": action["path"], "error": str(exc)}
    # `bytes` = payload WRITTEN this action; `size` = the file's total size AFTER it. An
    # append that truly appended shows size == prior + bytes; an overwrite shows size ==
    # bytes — so append-vs-overwrite is provable from the observation alone.
    return {"kind": "write_file", "path": action["path"], "bytes": len(data.encode("utf-8")),
            "append": bool(action.get("append")), "size": size}


def _nearest_anchor_hint(text: str, anchor: str) -> str:
    """When an anchor doesn't match, find the closest ACTUAL line and show it via repr() — so a
    near-miss on an invisible or ambiguous character (a non-ASCII dash like — vs -, a NBSP, a
    tab-vs-spaces, trailing whitespace) is diagnosable at a glance instead of by trial and error.
    read_file renders such characters escaped, so copying the displayed anchor can silently differ
    from the file's real bytes; repr() of the true line shows exactly what to copy. Empty string
    when nothing is close enough (a genuinely absent anchor gets no misleading hint).
    """
    first = anchor.splitlines()[0].strip() if anchor.strip() else ""
    if not first:
        return ""
    lines = text.splitlines()
    match = difflib.get_close_matches(first, [ln.strip() for ln in lines], n=1, cutoff=0.7)
    if not match:
        return ""
    # the raw line whose stripped form matched — show it verbatim via repr()
    actual = next((ln for ln in lines if ln.strip() == match[0]), match[0])
    return (f". Closest line in the file is {actual!r} — note any character that differs from "
            "your anchor (a non-ASCII dash, NBSP, tab vs spaces, or trailing whitespace); "
            "repr() shows the true bytes to copy.")


def do_edit_file(action: dict, ctx: RunContext) -> dict:
    """Anchor-replace in place — revisions cost the diff, not the whole document (the
    write_file counterpart for touching a few lines of a large file).
    """
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "edit_file", "path": action["path"], "error": err}
        if not path.is_file():
            return {"kind": "edit_file", "path": action["path"],
                    "error": "file does not exist — create it with write_file"}
        text = path.read_text(encoding="utf-8")
        anchor = str(action["anchor"])
        replacement = str(action.get("replacement") or "")
        count = text.count(anchor)
        if count == 0:
            return {"kind": "edit_file", "path": action["path"],
                    "error": "anchor not found in the file — copy it VERBATIM from a "
                             "read_file observation (whitespace and line breaks included)"
                             + _nearest_anchor_hint(text, anchor)}
        if count > 1 and not action.get("all"):
            return {"kind": "edit_file", "path": action["path"],
                    "error": f"anchor appears {count} times — extend it until it is unique, "
                             "or set all: true to replace every occurrence"}
        new_text = text.replace(anchor, replacement) if action.get("all") \
            else text.replace(anchor, replacement, 1)
        # F460: a structured file may not stop parsing because of an edit — refuse before
        # the write, so the file is untouched and the run is told on the turn it erred.
        if err := fileformat.check_after(path, text, new_text):
            return {"kind": "edit_file", "path": action["path"], "error": err}
        # Atomic + mode-preserving (the file exists — checked above), same reasoning as
        # do_write_file: no torn read/commit for a concurrent reader of this routine's dir.
        atomic_write(path, new_text, mode=path.stat().st_mode & 0o7777)
        ctx.seen_paths.add(str(path))   # an anchored edit is grounded by its verbatim anchor
        _note_recorded_phase(ctx, path)   # a cursor patched in place counts too (F563)
    except (OSError, PermissionError) as exc:
        return {"kind": "edit_file", "path": action["path"], "error": str(exc)}
    return {"kind": "edit_file", "path": action["path"],
            "replacements": count if action.get("all") else 1,
            "bytes": len(new_text.encode("utf-8"))}


# ---- native filesystem actions (D120=A): delete / move / mkdir --------------------
# Same jail and same seals as write_file (resolve_rel against the write roots, _write_gate
# for runs/ / .util_outputs/ / routine.yaml / the recipe), plus the destructive-op grounding
# rule: removing or relocating a path OUTSIDE the routine's own dir requires having seen it
# this run — the same reasoning as write_file's overwrite gate, extended to deletion.

def _tree_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _unseen_destruction(ctx: RunContext, resolved, what: str) -> str | None:
    """The grounding gate for delete and move-src: destroying a path outside the routine's
    own dir that this run has never read. The own dir is exempt (state cleanup is a
    routine's normal mode); elsewhere the model must have LOOKED at what it destroys — a
    read_file of the path, which for a directory is its listing and for a binary or
    oversized file its size (the refusal grounds too). A shell `ls` does not count: the
    engine cannot see what a shell command showed, only what read_file returned. The gate
    text names the two forms because a run that reads "read_file it first" about a season
    pack once read_file'd a 1.5 GB .mkv to comply (2026-09-14).
    """
    if resolved.is_relative_to(ctx.routine.dir) or str(resolved) in ctx.seen_paths:
        return None
    return (f"this {what} a path outside the routine's own dir that this run has never "
            "read — read_file it first (a directory reads as its listing, a binary or "
            "oversized file as its size: both count), then remove it knowingly, so a stray "
            "call cannot destroy something sight-unseen")


def do_delete(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "delete", "path": action["path"], "error": err}
        if not path.exists() and not path.is_symlink():
            return {"kind": "delete", "path": action["path"],
                    "error": "no such path — read_file its parent directory (a directory "
                             "reads as its listing) to see what is actually there"}
        if len(path.parts) == 1:                     # the filesystem root is its only part
            return {"kind": "delete", "path": action["path"],
                    "error": "refusing to delete a filesystem-root path"}
        if err := _unseen_destruction(ctx, path, "deletes"):
            return {"kind": "delete", "path": action["path"], "error": err}
        if path.is_dir() and not path.is_symlink():
            if not action.get("recursive"):
                return {"kind": "delete", "path": action["path"],
                        "error": "path is a directory — pass recursive: true to remove the "
                                 "whole tree (a stray call must not be able to wipe one)"}
            freed = _tree_size(path)
            what = "dir"
            shutil.rmtree(path)
        else:
            freed = path.stat().st_size
            what = "file"
            path.unlink()
    except (OSError, PermissionError) as exc:
        return {"kind": "delete", "path": action["path"], "error": str(exc)}
    return {"kind": "delete", "path": action["path"], "type": what,
            "bytes_freed": freed, "removed": True}


def do_move(action: dict, ctx: RunContext) -> dict:
    try:
        src = resolve_rel(ctx.routine.dir, action["src"], ctx.write_roots())
        dst = resolve_rel(ctx.routine.dir, action["dst"], ctx.write_roots())
        for p, field in ((src, "src"), (dst, "dst")):
            if err := _write_gate(ctx, p):
                return {"kind": "move", "src": action["src"], "dst": action["dst"],
                        "error": f"{field}: {err}"}
        if not src.exists() and not src.is_symlink():
            return {"kind": "move", "src": action["src"], "dst": action["dst"],
                    "error": "no such source path — read_file its parent directory (a "
                             "directory reads as its listing) to see what is actually there"}
        if dst.exists():
            return {"kind": "move", "src": action["src"], "dst": action["dst"],
                    "error": "destination already exists — move refuses to overwrite; "
                             "delete it first if the replacement is really wanted"}
        if err := _unseen_destruction(ctx, src, "moves"):
            return {"kind": "move", "src": action["src"], "dst": action["dst"], "error": err}
        moved = _tree_size(src) if src.is_dir() and not src.is_symlink() \
            else src.stat().st_size
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except (OSError, PermissionError) as exc:
        return {"kind": "move", "src": action["src"], "dst": action["dst"], "error": str(exc)}
    return {"kind": "move", "src": action["src"], "dst": action["dst"],
            "bytes_moved": moved, "moved": True}


def do_mkdir(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "mkdir", "path": action["path"], "error": err}
        existed = path.is_dir()
        if existed and not action.get("parents"):
            return {"kind": "mkdir", "path": action["path"],
                    "error": "path already exists — pass parents: true to treat that as "
                             "success"}
        if action.get("parents"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir()
    except FileExistsError as exc:
        return {"kind": "mkdir", "path": action["path"], "error": str(exc)}
    except (OSError, PermissionError) as exc:
        return {"kind": "mkdir", "path": action["path"], "error": str(exc)}
    return {"kind": "mkdir", "path": action["path"], "created": not existed}


