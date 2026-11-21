"""READING a document — the PDF or Office file read_file meets is converted, not refused.

`read_file` hands a PDF, Word, PowerPoint or Excel file to the library's `doc-read` util
(DOC_UTIL, one engine-owned name like `mediaops.VISION_UTIL`) and pages the Markdown it produced
like a text file: `start_line`, `max_lines` and `start_char` address the CONVERTED text, and the
observation says it is a conversion and names PDF pages with no text layer (reported, never
silently OCR'd).

A file is a document by its MAGIC BYTES (`document_kind`), never its name: `%PDF-` at byte 0,
or a zip holding `[Content_Types].xml` plus a `word/`, `ppt/` or `xl/` part.

The conversion is the ENGINE's call, inside the run's own jail: the run's fs roots, no secret,
and `offline`. The util writes Markdown to a file and prints only a summary, so the engine never
holds the document or its conversion as one string.

The util writes into a private DROP DIRECTORY in the system temp dir, not the cache: `.doc_cache/`
is sealed against jailed subprocesses (`ownseal.SEALED_DIRS`, D179), and the util atomic-writes
BESIDE its `--out` target, so it needs CREATE in the parent. The engine moves the result into
the cache afterwards (`_store`).

A document over `fileops.READ_MAX_BYTES` is still converted — what is read is the streamed
conversion — bounded instead by MAX_PAGES, the util's own Office cap, and CONVERT_TIMEOUT_S.

THE CACHE (`CACHE_DIR`) is keyed by the sha256 of the file's BYTES; engine-owned, read-only
for the run, gitignored, never search-indexed, pruned to KEEP_DOCS most recently read. A failed
conversion is never cached: read_file's binary refusal carries the failure and the next read
tries again.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .. import sandbox, utils_lib, utils_run
from ..paths import atomic_write_json, ensure_gitignored, read_json
from .run_context import work_dir

if TYPE_CHECKING:
    from .run_context import RunContext

DOC_UTIL = "doc-read"
CACHE_DIR = ".doc_cache"
#: Prefix of the per-conversion DROP DIRECTORY in the system temp dir (which
#: `sandbox._toolchain` mounts read-write for every util); named so `_prune` can remove one a
#: killed conversion left behind.
DROP_PREFIX = "rsched-docread-"
MAX_PAGES = 200
CONVERT_TIMEOUT_S = 300
KEEP_DOCS = 32
#: The formats `document_kind` names, as the observation says them.
FORMATS = {"pdf": "a PDF", "docx": "a Word document", "pptx": "a PowerPoint deck",
           "xlsx": "an Excel workbook"}
_OOXML_PARTS = {"word/": "docx", "ppt/": "pptx", "xl/": "xlsx"}
_HASH_CHUNK = 1024 * 1024


def document_kind(path: Path, head: bytes) -> str | None:
    """`pdf` / `docx` / `pptx` / `xlsx` from the file's first bytes (`head`), or None. A zip
    is opened only to list its central directory, which decompresses nothing. The util carries
    its own copy, held to this one by tests/test_docread.py.
    """
    if head.startswith(b"%PDF-"):
        return "pdf"
    if not head.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    return next((kind for prefix, kind in _OOXML_PARTS.items()
                 if any(n.startswith(prefix) for n in names)), None)


@dataclass(frozen=True, slots=True)
class Conversion:
    """A converted document: the Markdown file read_file pages and the util's summary of it."""

    text: Path
    meta: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _run_util(ctx: RunContext, src: Path, out: Path) -> tuple[int, str, str]:
    """The one util call: `doc-read SRC --out OUT --max-pages N --json`, in the run's jail."""
    home = ctx.server.libraries_home
    return utils_run.run_util(
        home, DOC_UTIL, [str(src), "--out", str(out), "--max-pages", str(MAX_PAGES), "--json"],
        timeout=CONVERT_TIMEOUT_S, policy=sandbox.policy_for_ctx(ctx),
        withhold_secrets=utils_run.util_needs(home, DOC_UTIL).secrets, offline=True,
        cwd=work_dir(ctx), aborted=ctx.aborted)


def _store(part: Path, text: Path) -> None:
    """Move the util's Markdown from its drop directory into the cache — the half of the
    conversion the ENGINE performs, because only the engine may write there.

    Copied under a dot-name then renamed INSIDE the cache (a rename across filesystems is
    `EXDEV`), so a reader sees the whole conversion or no file; `_prune` sweeps the dot-name
    if interrupted.
    """
    fd, tmp_name = tempfile.mkstemp(dir=text.parent, prefix=f".{text.name}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(part, tmp)
        tmp.replace(text)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _summary(out: str) -> dict | None:
    """The util's JSON summary, or None when it printed something else."""
    try:
        meta = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    return meta if isinstance(meta, dict) and meta.get("format") in FORMATS else None


def _prune(base: Path) -> None:
    """Keep the KEEP_DOCS most recently READ conversions (a hit refreshes its mtime), and drop
    what a killed conversion left behind (older than two conversion deadlines): a half-written
    dotfile in the cache and a stale DROP DIRECTORY. Best effort.
    """
    stale = time.time() - 2 * CONVERT_TIMEOUT_S
    try:
        docs = sorted(base.glob("*.md"), key=lambda p: p.stat().st_mtime_ns)
        for old in docs[:-KEEP_DOCS]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        for leftover in base.glob(".*"):
            if leftover.is_file() and leftover.stat().st_mtime < stale:
                leftover.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        for drop in Path(tempfile.gettempdir()).glob(f"{DROP_PREFIX}*"):
            if drop.is_dir() and drop.stat().st_mtime < stale:
                shutil.rmtree(drop, ignore_errors=True)
    except OSError:
        return


def converted(ctx: RunContext, path: Path, kind: str) -> Conversion | str:
    """The document at `path` as Markdown on disk — from the cache, or converted now — or WHY
    it could not be (a sentence the refusal carries). Never raises for a file problem.
    """
    try:
        digest = _sha256(path)
    except OSError as exc:
        return f"it could not be read to convert it: {exc}"
    base = ctx.routine.dir / CACHE_DIR
    text, meta_file = base / f"{digest}.md", base / f"{digest}.json"
    meta = read_json(meta_file)
    if isinstance(meta, dict) and text.is_file():
        try:
            os.utime(text)                      # recently read: the last one pruned
        except OSError:
            pass
        return Conversion(text, meta)
    if not utils_lib.exists(ctx.server.libraries_home, DOC_UTIL):
        return f"the `{DOC_UTIL}` util that converts {FORMATS[kind]} is not installed"
    try:
        ensure_gitignored(ctx.routine.dir, CACHE_DIR,
                          "documents read_file converted to Markdown (engine-owned, pruned)")
        base.mkdir(parents=True, exist_ok=True)
        drop = Path(tempfile.mkdtemp(prefix=f"{DROP_PREFIX}{digest[:12]}."))
    except OSError as exc:
        return f"the conversion cache could not be prepared: {exc}"
    part = drop / f"{digest}.md"
    try:
        code, out, err = _run_util(ctx, path, part)
        summary = _summary(out) if code == 0 else None
        if summary is None:
            said = " ".join((err or out or "").split())[-600:]
            return (f"the {DOC_UTIL} util failed (exit {code}): {said or 'no output'}"
                    if code != 0 else f"the {DOC_UTIL} util returned no summary: {said}")
        _store(part, text)
        # The util reported the drop path it was given, which this call removes on its way
        # out; the cached summary must name where the conversion actually lives.
        atomic_write_json(meta_file, {**summary, "out": str(text)})
    except OSError as exc:
        return f"the converted text could not be stored: {exc}"
    finally:
        shutil.rmtree(drop, ignore_errors=True)
    _prune(base)
    return Conversion(text, summary)


def _window_pages(page_lines: list, start: int, end: int) -> list[int] | None:
    """[first, last] PDF page the lines [start, end] of the converted text belong to."""
    starts = sorted((int(line), int(page)) for page, line in page_lines)
    inside = [page for line, page in starts if line <= end]
    if not inside or start > end:
        return None
    first = max((page for line, page in starts if line <= start), default=inside[0])
    return [first, inside[-1]]


def described(meta: dict, start: int, end: int) -> dict:
    """What the observation says about a conversion — the format, and for a PDF the pages
    converted, the window's own pages, the cut and the pages that have no text layer.
    """
    out: dict = {"from": meta["format"]}
    if meta["format"] != "pdf":
        return out
    try:
        pages = [int(p) for p in meta["pages"]]
        out.update(pages=pages, pages_total=int(meta["pages_total"]),
                   capped=bool(meta.get("capped")),
                   no_text_layer=[int(p) for p in meta.get("no_text_layer") or []])
        if window := _window_pages(meta.get("page_lines") or [], start, end):
            out["window_pages"] = window
    except (KeyError, TypeError, ValueError):
        return {"from": "pdf"}
    return out


def _ranges(pages: list[int], limit: int = 12) -> str:
    """`5, 9, 31-60, 88` — consecutive pages folded, at most `limit` runs named."""
    runs: list[list[int]] = []
    for p in sorted(pages):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    shown = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs[:limit]]
    more = f", … ({len(runs) - limit} more runs)" if len(runs) > limit else ""
    return ", ".join(shown) + more


def clause(c: dict) -> str:
    """The conversion as the observation head says it (obs_files)."""
    out = f"converted from {FORMATS[c['from']]} to Markdown (layout approximated)"
    if "pages" not in c:
        return out
    first, last = c["pages"]
    total = c["pages_total"]
    out += f", pages {first}-{last} of {total}"
    if window := c.get("window_pages"):
        out += f", this window pages {window[0]}-{window[1]}"
    if c.get("capped"):
        out += (f"; pages {last + 1}-{total} NOT converted — one read converts at most "
                f"{MAX_PAGES} pages; the {DOC_UTIL} util converts any window (--pages "
                f"{last + 1}-{min(last + MAX_PAGES, total)} --out <file>, then read_file that "
                "file)")
    if blind := c.get("no_text_layer"):
        out += (f"; {len(blind)} page(s) have NO TEXT LAYER (scanned images, not "
                f"transcribed): {_ranges(blind)} — view_image sees them, the {DOC_UTIL} "
                "util's --ocr transcribes them")
    return out
