"""File-shaped effect handlers: read_file / write_file / edit_file — plus the path gates every
file-shaped action shares (the runs/ read depth, the write-grounding rule, the seals on what
the engine and the operator own). delete / move / mkdir live in fsops.py, `view_image` in
mediaops.py, document conversion in docread.py, the name-addressed stores in memops.py.

Every gate compares RESOLVED paths, anchored on every routine dir the run can REACH
(`reach.routine_dirs`), not only its own: a child's workspace roots extend the parent's tree.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .. import rundepth
from ..grants import CONFIG_FILE
from ..paths import atomic_write, read_json
from ..readmodels.statemap import STAGES_DIR
from . import docread, fileformat, grounding, readwindow, storeseal
from .finishline import FILE as FINISH_LINE
from .outputs import OUTPUTS_DIR
from .reach import routine_dirs
from .run_context import RunContext, resolve_action_path

if TYPE_CHECKING:
    from collections.abc import Iterator

# The largest file read_file will OPEN; a larger text file is paged with shell or a util.
READ_MAX_BYTES = 8 * 1024 * 1024
BINARY_SNIFF_BYTES = 8 * 1024        # a NUL byte in the first 8 KiB marks a file binary
UTIL_DEFAULT_TIMEOUT_S = 300


def _own_dir(ctx: RunContext) -> Path:
    """The run's own dir, resolved — the one the grounding rules exempt."""
    return ctx.routine.dir.resolve()


def _within(resolved: Path, sealed: Path) -> bool:
    """Does the action path lie in (or at) `sealed`, taken at its own place or at what it
    resolves to? An action path is resolved — wholly, or up to its final entry for delete and
    move (`_entry`) — so a sealed path that is itself a link is met at its target by one and
    at its own place by the other.
    """
    return resolved.is_relative_to(sealed) or resolved.is_relative_to(sealed.resolve())


def _listing_lines(path: Path) -> Iterator[str]:
    """A directory's content IS its entries: one line each — `dir` / `file` / `link`, a
    file's size in bytes, the name — sorted by name, so a listing pages like a file and a
    directory is LOOKED AT without decoding anything in it.
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


def _head(path: Path) -> tuple[int, bytes]:
    """(size, the first 8 KiB) from a stat and one bounded read — everything known about a
    file before a single byte of it is decoded.
    """
    size = path.stat().st_size
    with path.open("rb") as fh:
        return size, fh.read(BINARY_SNIFF_BYTES)


def _sniff(path: Path) -> tuple[int, bool]:
    """(size, binary?) — a NUL byte in the head marks a file binary."""
    size, head = _head(path)
    return size, b"\0" in head


def _binary_error(size: int) -> str:
    return (f"binary file ({size:,} bytes) — read_file shows text only; "
            "view_image sees an image or PDF, a util or shell handles the rest"
            + grounding.REFUSAL_GROUNDS)


def _refusal(rel_path: str, size: int, binary: bool) -> dict | None:
    """The two reads that must never happen, decided from a stat and an 8 KiB sniff BEFORE
    anything is decoded: a binary file (a NUL byte in its head) and a file over
    READ_MAX_BYTES. Both carry `size`, so the refusal counts as having LOOKED at the file
    for the destruction gate (history.seen_paths reads the key back on resume). A DOCUMENT is
    converted instead (docread.py) and refused only when that fails.
    """
    if binary:
        return {"path": rel_path, "size": size, "error": _binary_error(size)}
    if size > READ_MAX_BYTES:
        return {"path": rel_path, "size": size,
                "error": f"{size:,} bytes exceeds the read_file cap of {READ_MAX_BYTES:,} "
                         "bytes — page it with shell (head / sed -n) or a util instead"
                         + grounding.REFUSAL_GROUNDS}
    return None


def _document_refusal(rel_path: str, size: int, kind: str, why: str) -> dict:
    """A document whose conversion failed is refused as the binary it is, plus what failed —
    the util missing, failing or out of time — so the run knows the read can work and why it
    did not this time. Never cached, so the next read tries again.
    """
    return {"path": rel_path, "size": size,
            "error": f"{_binary_error(size)}. It is {docread.FORMATS[kind]}, which read_file "
                     f"converts to Markdown, and that failed: {why}"}


def _read_one(rel_path: str, action: dict, ctx: RunContext) -> dict:
    start, asked = int(action.get("start_line") or 1), int(action.get("max_lines") or 0)
    start_char = max(0, int(action.get("start_char") or 0))
    directory = False
    conversion: docread.Conversion | None = None
    try:
        path = resolve_action_path(ctx, rel_path)
        if err := _memory_gate(ctx, path) or rundepth.read_gate(ctx, path):
            return {"path": rel_path, "error": err}
        if path.is_dir():
            directory = True
            window, total, first = readwindow.window(_listing_lines(path), start, asked)
        else:
            size, head = _head(path)
            source = path
            if kind := docread.document_kind(path, head):
                # a PDF / Office document: its Markdown conversion is what gets paged
                done = docread.converted(ctx, path, kind)
                if isinstance(done, str):
                    ctx.seen_paths.add(str(path))   # a stat IS a look, as for any refusal
                    return _document_refusal(rel_path, size, kind, done)
                conversion, source = done, done.text
            elif refusal := _refusal(rel_path, size, b"\0" in head):
                ctx.seen_paths.add(str(path))   # a stat IS a look: grounds delete/move/overwrite
                return refusal
            # streamed, never materialised: only the window is ever held in memory
            with source.open(encoding="utf-8", errors="replace") as fh:
                lines = (line.rstrip("\n") for line in fh)
                window, total, first = readwindow.window(lines, start, asked)
    except (OSError, PermissionError) as exc:
        return {"path": rel_path, "error": str(exc)}
    ctx.seen_paths.add(str(path))
    # Reading a stage module IS the run's phase transition, tracked here with zero recipe
    # cooperation; stage modules are the phase graph's nodes, so the names always match.
    if (path.suffix == ".md" and path.parent.name == STAGES_DIR
            and path.parent.parent == _own_dir(ctx)):
        ctx.phase = path.stem
        # …and the PATH taken, not just the current node.
        if path.stem not in ctx.phases_entered:
            ctx.phases_entered.append(path.stem)
    if start_char and window:
        # entering the first line part-way: the continuation of a line longer than the cap
        window = [window[0][start_char:], *window[1:]]
    obs = readwindow.windowed(rel_path, window, total, first, asked, start_char)
    if directory:
        obs["directory"] = True
    if conversion is not None:
        obs["converted"] = docread.described(conversion.meta, start, obs["end_line"])
    return obs

def _note_recorded_phase(ctx: RunContext, path) -> None:
    """Record a phase the run WROTE for itself — the second half of stage coverage.

    The read stamp sees a stage only when the run re-reads `stages/<name>.md`; a run that
    routes by writing `state/phase.json` is noticed here. Deliberately quiet: a malformed
    cursor is not evidence, and a write never fails over its content. Validating the name is
    `stage_coverage`'s job.
    """
    if path.name != "phase.json" or path.parent != _own_dir(ctx) / "state":
        return
    data = read_json(path)
    if not isinstance(data, dict):
        return          # a cursor that is not an object carries no phase — see the docstring
    phase = data.get("phase")
    if isinstance(phase, str) and phase and phase not in ctx.phases_recorded:
        ctx.phases_recorded.append(phase)


def do_read_file(action: dict, ctx: RunContext) -> dict:
    paths = action.get("paths")
    if paths:  # batched read: several files in ONE action, one entry each
        return {"kind": "read_file", "files": [_read_one(str(p), action, ctx) for p in paths]}
    return {"kind": "read_file", **_read_one(action["path"], action, ctx)}


MEMORY_REFUSAL = (".memory/ is reachable only through memory_read / memory_write — the engine "
                  "owns .memory/INDEX.md (built from each note's 'about' line), which a generic "
                  "file action would bypass")


def _memory_gate(ctx: RunContext, resolved) -> str | None:
    """The `.memory/` seal, on the RESOLVED path — where its siblings live.

    `engine/actions.py` also tests it LEXICALLY — a cheap correction, not a seal: a
    traversal or an absolute path walks past it.
    """
    for d in routine_dirs(ctx):
        if _within(resolved, d / ".memory"):
            return MEMORY_REFUSAL
        # …and a JOB's notebook (rsched/jobs.py): an open job's memory actions resolve in
        # its workspace, so every `.memory/` under the routine is the engine's alike
        for base in (d, d.resolve()):
            if resolved.is_relative_to(base) and ".memory" in resolved.relative_to(base).parts:
                return MEMORY_REFUSAL
    return None


FINISH_LINE_REFUSAL = ("the finish line is the operator's — a run reports against it in its "
                       "finish `accounting` (a distance, or met for an outcome the run proves) "
                       "and never edits it; if you believe it is wrong, file a report saying why")


def _finish_line_gate(ctx: RunContext, resolved) -> str | None:
    """The finish line decides when the routine RETIRES, so it is the operator's alone: a run
    answers for it through its finish accounting and never edits it.
    """
    if any(resolved in (d / FINISH_LINE, (d / FINISH_LINE).resolve())
           for d in routine_dirs(ctx)):
        return FINISH_LINE_REFUSAL
    return None


def _jobs_gate(ctx: RunContext, resolved) -> str | None:
    """The engine-owned stores (`storeseal`) — a file, or a directory and all beneath it."""
    for d in routine_dirs(ctx):
        for rel, why in storeseal.stores(ctx).items():
            if _within(resolved, d / rel):
                return why
    return None


def _removal_gate(ctx: RunContext, resolved: Path) -> str | None:
    """What a REMOVAL — delete, a move's source — may not take with it. Every other seal asks
    whether a path lies INSIDE something sealed; a removal also takes everything inside the
    path. A routine dir the run can reach is never removed whole, and neither is a directory
    holding its finish line or an engine-owned store.
    """
    for d in routine_dirs(ctx):
        if d.is_relative_to(resolved):
            return ("this would remove a routine's own directory, and its config, recipe, "
                    "memory and run history with it — remove what is inside it instead")
        line = d / FINISH_LINE
        if line.exists() and line.is_relative_to(resolved):
            return f"this would remove {FINISH_LINE}: {FINISH_LINE_REFUSAL}"
        for rel, why in storeseal.stores(ctx).items():
            if (d / rel).exists() and (d / rel).is_relative_to(resolved):
                return f"this would remove {rel}: {why}"
    return None


def _holds_config(resolved: Path) -> bool:
    """Is there a routine.yaml anywhere in this tree? Removing the tree writes that file too."""
    return (resolved.is_dir() and not resolved.is_symlink()
            and any(CONFIG_FILE in files for _root, _dirs, files in os.walk(resolved)))


def _engine_owned(ctx: RunContext, resolved) -> str | None:
    """`runs/` and `.util_outputs/` of every reachable routine dir: the engine's record of
    what happened, never rewritten by the run it records — and `.doc_cache/`, whose files a
    later read trusts by content hash. One exemption, only in the ROUTINE's runs/: a child's
    own workspace tree is where it WORKS. Everything else there (control.json, status.json,
    transcripts) is the engine's and the web's.
    """
    dirs = routine_dirs(ctx)
    workspace = dirs[-2] if len(dirs) > 1 else None     # the top-most child workspace
    for d in dirs:
        if _within(resolved, d / "runs") and not (
                d == dirs[-1] and workspace is not None and resolved.is_relative_to(workspace)):
            return "runs/ is engine-owned and read-only for the run"
        if _within(resolved, d / OUTPUTS_DIR):
            return (f"{OUTPUTS_DIR}/ is engine-owned and read-only for the run — it is the "
                    "saved full text of util output too large for its observation (read_file "
                    "it); a run does not rewrite the record of what a util returned")
        if _within(resolved, d / docread.CACHE_DIR):
            return (f"{docread.CACHE_DIR}/ is engine-owned and read-only for the run — it "
                    "holds the Markdown read_file converted documents to; read_file the "
                    "document itself, and write what you derive from it elsewhere")
    return None


def _write_gate(ctx: RunContext, resolved, *, creates: bool = True) -> str | None:
    """Backstop for engine-owned and permission-gated writes (GrantPolicy.deny handles the
    relative-path form; this catches absolute paths and traversals into every routine dir
    the run can reach — `routine_dirs`).

    `creates` is False for a path the action only REMOVES (delete, a move's source): a note
    left in a shared store for a routine that does not share it is refused when it is written;
    clearing one that is already stranded there is the repair, not the defect. A removal is
    held to what it would take WITH it instead (`_removal_gate`).
    """
    # All structural, not grants: they hold with no policy loaded.
    err = (_memory_gate(ctx, resolved) or _finish_line_gate(ctx, resolved)
           or _jobs_gate(ctx, resolved))
    if err is None and creates:
        from ..recipients import note_refusal

        err = note_refusal(ctx.server, resolved)
    elif err is None:
        err = _removal_gate(ctx, resolved)
    if err:
        return err
    if err := _engine_owned(ctx, resolved):
        return err
    # routine.yaml is config — never writable by ANY run, even with the recipe unlocked.
    if resolved.name == CONFIG_FILE or (not creates and _holds_config(resolved)):
        return ("routine.yaml is config (permissions, capabilities, budgets, roots) — no run "
                "edits it, not even the routine-improver (machine-tunable knobs live in "
                "tuning.yaml); file a deferred ask_user instead")
    # Only the RECIPE seal reads the policy; the two above are structural.
    if (g := ctx.grants) is None:
        return None
    if not g.recipe_unlocked:
        from ..grantpolicy import is_recipe_path

        if any(resolved.is_relative_to(d) and is_recipe_path(str(resolved.relative_to(d)))
               for d in routine_dirs(ctx)):
            return ("editing this routine's own recipe (main.md / stages/ / tuning.yaml) "
                    "needs the recipe-authoring permission, which this routine does not "
                    "hold — its instructions are the user's. File a deferred ask_user (or a "
                    "report) describing the change instead")
    return None


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_action_path(ctx, action["path"], write=True)
        if err := _write_gate(ctx, path):
            return {"kind": "write_file", "path": action["path"], "error": err}
        # Grounding gate: overwriting a file OUTSIDE the own dir requires having seen it this
        # run. The own dir, append and a new file are exempt.
        if (path.is_file() and not action.get("append")
                and not path.is_relative_to(_own_dir(ctx))
                and str(path) not in ctx.seen_paths):
            return {"kind": "write_file", "path": action["path"],
                    "error": grounding.OVERWRITE_REFUSAL}
        path.parent.mkdir(parents=True, exist_ok=True)
        data = action["content"]
        if not isinstance(data, str):
            # Structured content is serialized here. An APPENDED value is a RECORD: one
            # compact line, so a JSONL ledger stays one-object-per-line.
            data = (json.dumps(data, ensure_ascii=False) if action.get("append")
                    else json.dumps(data, indent=2, ensure_ascii=False)) + "\n"
        terminated = False
        if action.get("append"):
            # In a record-per-line file an unterminated string body would extend the previous
            # record, so it is terminated; prose files stay byte-faithful.
            if data and not data.endswith("\n") and fileformat.record_per_line(path):
                data += "\n"
                terminated = True
            with path.open("a", encoding="utf-8") as fh:
                fh.write(data)
        else:
            # Only a STR body over an existing file can break a format; the old text is read
            # only for a format the gate checks, never decoded otherwise.
            if (isinstance(action["content"], str) and fileformat.checks(path)
                    and path.is_file()
                    and (err := fileformat.check_after(
                        path, path.read_text(encoding="utf-8", errors="replace"), data))):
                return {"kind": "write_file", "path": action["path"], "error": err}
            # Atomic (tmp+rename): a concurrent reader or git autocommit never sees a torn write.
            keep = path.stat().st_mode & 0o7777 if path.exists() else None
            atomic_write(path, data, mode=keep)
        ctx.seen_paths.add(str(path))   # written = seen: a rewrite of own output is grounded
        _note_recorded_phase(ctx, path)   # the run's own cursor is stage-coverage evidence
        size = path.stat().st_size      # TOTAL bytes on disk after the write
    except (OSError, PermissionError) as exc:
        return {"kind": "write_file", "path": action["path"], "error": str(exc)}
    # `bytes` = the payload written; `size` = the total AFTER it (append vs overwrite);
    # `unparsed` = a body that should parse and does not (a cut-short generation).
    return {"kind": "write_file", "path": action["path"], "bytes": len(data.encode("utf-8")),
            "append": bool(action.get("append")), "size": size,
            **({"terminated": True} if terminated else {}),  # separator supplied
            **fileformat.unparsed(path, data)}


def _nearest_anchor_hint(text: str, anchor: str) -> str:
    """When an anchor doesn't match, show the closest ACTUAL line via repr(), so a near-miss on
    an invisible or ambiguous character is diagnosable at a glance. Empty when nothing is close.
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


def _edit_source(path: Path) -> tuple[str, str]:
    """(the file's text, "") — or ("", why edit_file will not take it).

    An edit holds the whole text and its replaced copy, so it refuses the two files read_file
    refuses — a binary one and one over READ_MAX_BYTES — from the same stat and sniff, before
    any decode. A file that is not UTF-8 is refused too, never decoded lossily: writing back
    an `errors="replace"` decode would corrupt every byte it could not read.
    """
    size, binary = _sniff(path)
    if binary or size > READ_MAX_BYTES:
        what = "a binary file" if binary else f"{size:,} bytes, over the {READ_MAX_BYTES:,} cap"
        return "", (f"{what} — edit_file rewrites UTF-8 text in place; change this one with "
                    "shell or a util instead")
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError as exc:
        return "", (f"not UTF-8 text ({exc.reason} at byte {exc.start}) — edit_file rewrites "
                    "UTF-8 text in place and will not re-encode this file; change it with "
                    "shell or a util instead")


def do_edit_file(action: dict, ctx: RunContext) -> dict:
    """Anchor-replace in place — revisions cost the diff, not the whole document (the
    write_file counterpart for touching a few lines of a large file).
    """
    try:
        path = resolve_action_path(ctx, action["path"], write=True)
        if err := _write_gate(ctx, path):
            return {"kind": "edit_file", "path": action["path"], "error": err}
        if not path.is_file():
            return {"kind": "edit_file", "path": action["path"],
                    "error": "file does not exist — create it with write_file"}
        text, refused = _edit_source(path)
        if refused:
            return {"kind": "edit_file", "path": action["path"], "error": refused}
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
        # A structured file may not stop parsing because of an edit — refuse before the write.
        if err := fileformat.check_after(path, text, new_text):
            return {"kind": "edit_file", "path": action["path"], "error": err}
        # Atomic + mode-preserving, as in do_write_file.
        atomic_write(path, new_text, mode=path.stat().st_mode & 0o7777)
        ctx.seen_paths.add(str(path))   # an anchored edit is grounded by its verbatim anchor
        _note_recorded_phase(ctx, path)   # a cursor patched in place counts too
    except (OSError, PermissionError) as exc:
        return {"kind": "edit_file", "path": action["path"], "error": str(exc)}
    return {"kind": "edit_file", "path": action["path"],
            "replacements": count if action.get("all") else 1,
            "bytes": len(new_text.encode("utf-8"))}
