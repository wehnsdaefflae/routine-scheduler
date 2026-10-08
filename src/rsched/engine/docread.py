"""READING a document — the PDF or Office file read_file meets is converted, not refused.

A PDF, a Word document, a PowerPoint deck or an Excel workbook is not text, so read_file once
refused it like any binary, and a run that had to read a contract or a handout either looked
at it page by page as a picture or guessed at a util. `read_file` now hands such a file to the
library's `doc-read` util (DOC_UTIL — ONE engine-owned name, the precedent `mediaops.VISION_UTIL`
set) and pages the Markdown it produced exactly like a text file: `start_line`, `max_lines` and
`start_char` address the CONVERTED text, the observation says it is a conversion (a layout
rendered as Markdown is approximate) and names the PDF pages that carry no text layer, which a
conversion reports and never silently OCRs.

What makes a file a document is its MAGIC BYTES (`document_kind`): `%PDF-` at byte 0, or a zip
whose central directory holds `[Content_Types].xml` plus a `word/`, `ppt/` or `xl/` part —
never its name. Anything else keeps read_file's text path and its two refusals.

THE CONVERSION IS THE ENGINE'S CALL, made on the run's behalf inside the run's own jail: the
run's fs roots, the util's `fs: roots`, no secret at all (the call never OCRs, so the vision
key the util's tree declares is withheld), and `offline` — TCP denied although the util's
`calls: vision` makes its tree `net: outbound`. The util writes the Markdown straight into the
cache file and prints only a summary, so the engine never holds the document, nor its
conversion, as one string: read_file streams the cache file line by line like any text.

A DOCUMENT OVER `fileops.READ_MAX_BYTES` IS STILL CONVERTED. That cap exists because a read
decoded the file it read; here what is read is the converted text, streamed from disk, and
the source is hashed in 1 MiB chunks. The conversion bounds itself instead: at most MAX_PAGES
pages per conversion (the observation names the rest and how to reach them), the util's own
cap on an Office file (its converter holds the whole document), and CONVERT_TIMEOUT_S.

THE CACHE (`CACHE_DIR`, beside `.util_outputs/`) is keyed by the sha256 of the file's BYTES,
so a re-read — the next window, the next run — converts nothing, and a changed file is a new
key. Engine-owned and read-only for the run (`fileops._engine_owned`), gitignored on first
use, never search-indexed, pruned to the KEEP_DOCS most recently read. A failed conversion is
never cached: the util missing, failing or timing out leaves read_file's binary refusal with
the failure appended (`fileops._read_one`), and the next read tries again.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    is opened only to list its central directory, which decompresses nothing — so a 1.5 GB
    archive costs one seek to its end. The util carries its own copy (it cannot import the
    engine), held to this one by tests/test_docread.py.
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


def _summary(out: str) -> dict | None:
    """The util's JSON summary, or None when it printed something else."""
    try:
        meta = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    return meta if isinstance(meta, dict) and meta.get("format") in FORMATS else None


def _prune(base: Path) -> None:
    """Keep the KEEP_DOCS most recently READ conversions (a hit refreshes its mtime), and drop
    the half-written temp files a conversion ended mid-write left behind (dotfiles older than
    two conversion deadlines — a live one is younger). Best effort: two runs pruning one
    routine's cache at once may each find a file already gone.
    """
    try:
        docs = sorted(base.glob("*.md"), key=lambda p: p.stat().st_mtime_ns)
        for old in docs[:-KEEP_DOCS]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        stale = time.time() - 2 * CONVERT_TIMEOUT_S
        for leftover in base.glob(".*"):
            if leftover.is_file() and leftover.stat().st_mtime < stale:
                leftover.unlink(missing_ok=True)
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
        fd, part_name = tempfile.mkstemp(dir=base, prefix=f".{digest}.", suffix=".part")
        os.close(fd)
    except OSError as exc:
        return f"the conversion cache could not be prepared: {exc}"
    part = Path(part_name)
    try:
        code, out, err = _run_util(ctx, path, part)
        summary = _summary(out) if code == 0 else None
        if summary is None:
            said = " ".join((err or out or "").split())[-600:]
            return (f"the {DOC_UTIL} util failed (exit {code}): {said or 'no output'}"
                    if code != 0 else f"the {DOC_UTIL} util returned no summary: {said}")
        part.replace(text)
        atomic_write_json(meta_file, summary)
    except OSError as exc:
        return f"the converted text could not be stored: {exc}"
    finally:
        part.unlink(missing_ok=True)
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
