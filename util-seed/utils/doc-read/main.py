# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf4llm>=1.28,<2", "markitdown[docx,pptx,xlsx]>=0.1.8,<0.2"]
# ///
"""doc-read — read a PDF or an Office file (docx/pptx/xlsx) as Markdown, offline, page by page.

usage: gu doc-read FILE [--pages A-B] [--max-pages N] [--ocr] [--out PATH] [--json]
calls: vision
tags: pdf, document, office, markdown, docx, pptx, xlsx, ocr
secrets: OPENROUTER_VISION_KEY?
net: none
fs: roots

A PDF becomes layout-aware Markdown (pymupdf4llm: headings, lists, tables, reading order)
with one `<!-- page N of M -->` marker line opening each page; a Word, PowerPoint or Excel
file becomes Markdown through markitdown. The format is decided by the file's MAGIC BYTES
(`%PDF-`, or an OOXML zip carrying `[Content_Types].xml`), never by its name. All of it runs
locally: no network and no model download.

PAGES. --pages takes ONE 1-based window (`3-7`, `12`, `40-`, `-5`); at most --max-pages
(default 200) pages are converted per call, and the summary says when the window was cut and
which page to continue from. Office files have no pages: --pages, --max-pages and --ocr apply
to PDFs only, and an Office file over OFFICE_MAX_BYTES (16 MiB) is refused, because its
converter holds the whole document in memory.

A SCANNED PAGE IS REPORTED, NEVER SILENTLY OCR'D. A page with no text layer but an image on
it (typically a scan) is listed in the summary (`no_text_layer`) and marked in the Markdown
where its text would be; an empty page is listed as `blank`. --ocr sends ONLY the no-text
pages of the window, each rendered to PNG, to the `vision` util (a cloud model billed to
OPENROUTER_VISION_KEY — an optional secret, needed by --ocr alone) and inlines what it read,
marked as OCR. A page that could not be read — no key, a refusal, the call's deadline
(RSCHED_UTIL_TIMEOUT_S) — stays marked and is named under `ocr.failed` / `ocr.skipped`.

--out writes the Markdown to PATH (atomically) instead of stdout. --json prints a summary:
format, pages_total, pages [first, last], capped, next_page, no_text_layer, blank,
page_lines ([page, line] — the line each page's marker sits on), chars, ocr (with --ocr),
and the markdown itself when there is no --out. Without --json the Markdown goes to stdout
and a one-line summary to stderr. Exit 1 with `error: …` on stderr for an unreadable file,
2 for a bad call. --selftest is offline: it builds a PDF, a docx, a pptx and an xlsx."""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

MAX_PAGES = 200
OFFICE_MAX_BYTES = 16 * 1024 * 1024
OFFICE = {"word/": "docx", "ppt/": "pptx", "xl/": "xlsx"}
OCR_DPI = 200
#: Fewer characters than this over an image standing in for the page is a scan, not a text
#: layer: a stamped page number or a Bates line, never the page's content.
SCAN_TEXT_CHARS = 40
OCR_PROMPT = ("Transcribe ALL text on this page verbatim, in reading order, as Markdown "
              "(headings, lists, tables). Add nothing that is not on the page.")
#: Kept back from the call's deadline: room to write what was converted before the runner
#: ends the util, and the least an OCR request is worth starting with.
DEADLINE_MARGIN_S = 15
OCR_MIN_S = 20


def detect(path: Path) -> str | None:
    """`pdf` / `docx` / `pptx` / `xlsx` from the file's magic bytes, or None. A zip is an
    OOXML document only when it carries `[Content_Types].xml` — reading the central directory
    decompresses nothing.
    """
    with path.open("rb") as fh:
        head = fh.read(8)
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
    return next((kind for prefix, kind in OFFICE.items()
                 if any(n.startswith(prefix) for n in names)), None)


def page_window(spec: str | None, total: int, max_pages: int) -> tuple[int, int, bool]:
    """(first, last, capped) — the 1-based pages to convert. Raises ValueError on a bad spec."""
    first, last = 1, total
    if spec:
        m = re.fullmatch(r"\s*(\d*)\s*(-?)\s*(\d*)\s*", spec)
        if not m or not (m.group(1) or m.group(3)) or (m.group(3) and not m.group(2)):
            raise ValueError(f"--pages {spec!r} is not a page window (3-7, 12, 40-, -5)")
        first = int(m.group(1)) if m.group(1) else 1
        last = (int(m.group(3)) if m.group(3) else total) if m.group(2) else first
    if first < 1 or first > last:
        raise ValueError(f"--pages {spec!r} names no page")
    if first > total:
        raise ValueError(f"page {first} is past the end — the document has {total} pages")
    last = min(last, total)
    capped = last - first + 1 > max_pages
    return first, (first + max_pages - 1 if capped else last), capped


def _deadline() -> float:
    """When this call must stop starting work: the runner's deadline less a margin, or ten
    minutes for a call made by hand. Taken when the call STARTS, so the conversion before the
    OCR is spent from the same clock the runner keeps."""
    try:
        budget = int(os.environ.get("RSCHED_UTIL_TIMEOUT_S", ""))
    except ValueError:
        budget = 0
    return time.monotonic() + (max(budget - DEADLINE_MARGIN_S, 5) if budget > 0 else 600)


def ask_vision(png: Path, left: float) -> str:
    """One page image through the vision util's OCR task — the text it read."""
    env = {**os.environ, "RSCHED_UTIL_TIMEOUT_S": str(int(left))}
    res = subprocess.run(["gu", "vision", str(png), "--task", "ocr", "--prompt", OCR_PROMPT,
                          "--json"], capture_output=True, text=True, timeout=left, env=env,
                         check=False)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip()[-300:] or f"exit {res.returncode}")
    return str(json.loads(res.stdout).get("text") or "")


def ocr(doc, pages: list[int], deadline: float) -> tuple[dict, dict]:
    """Read each no-text page through vision: ({page: text}, {"done", "failed", "skipped"})."""
    texts: dict[int, str] = {}
    report: dict = {"done": [], "failed": {}, "skipped": []}
    fatal = ""
    with tempfile.TemporaryDirectory(prefix="doc-read-") as tmp:
        for p in pages:
            left = deadline - time.monotonic()
            if fatal:
                report["failed"][str(p)] = fatal
                continue
            if left < OCR_MIN_S:
                report["skipped"].append(p)
                continue
            png = Path(tmp) / f"page-{p}.png"
            doc[p - 1].get_pixmap(dpi=OCR_DPI).save(str(png))
            try:
                texts[p] = ask_vision(png, left)
                report["done"].append(p)
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                report["failed"][str(p)] = str(exc)
                if (isinstance(exc, FileNotFoundError) or "OPENROUTER_VISION_KEY" in str(exc)
                        or "no util named" in str(exc)):
                    fatal = str(exc)       # every later page would fail the same way
    return texts, report


def _page_kind(page, rect_type) -> str:
    """`text`, `scan` or `blank`. A SCAN has (next to) no text layer and an image standing in
    for the page: one covering half of it or more, or any image at all on a page without a
    character. A scanner's stamped page number is not a text layer, so a few characters over
    a full-page image still read as a scan — and those characters are kept.
    """
    text = page.get_text("text").strip()
    if len(text) >= SCAN_TEXT_CHARS:
        return "text"
    images = page.get_image_info()
    half = abs(page.rect) / 2
    if (images and not text) or any(abs(rect_type(i["bbox"]) & page.rect) >= half
                                     for i in images):
        return "scan"
    return "text" if text else "blank"


def convert_pdf(path: Path, spec: str | None, max_pages: int, want_ocr: bool,
                deadline: float) -> tuple[str, dict]:
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(str(path))
    if doc.needs_pass:
        raise ValueError("the PDF is encrypted and needs a password")
    total = doc.page_count
    if total == 0:
        raise ValueError("the PDF has no pages")
    first, last, capped = page_window(spec, total, max_pages)
    window = list(range(first, last + 1))
    kinds = {p: _page_kind(doc[p - 1], pymupdf.Rect) for p in window}
    scans = [p for p in window if kinds[p] == "scan"]
    with contextlib.redirect_stdout(sys.stderr):      # the library prints notices
        chunks = pymupdf4llm.to_markdown(doc, pages=[p - 1 for p in window], page_chunks=True,
                                         use_ocr=False, show_progress=False)
    if len(chunks) != len(window):
        raise RuntimeError(f"the converter returned {len(chunks)} pages for {len(window)}")
    read, ocr_report = ocr(doc, scans, deadline) if want_ocr and scans else ({}, None)
    parts: list[str] = []
    page_lines: list[list[int]] = []
    line = 1
    for p, chunk in zip(window, chunks, strict=True):
        if p in read:
            head = (f"<!-- page {p} of {total}: NO TEXT LAYER — the text below was read by OCR "
                    "(vision); verify it before relying on it -->")
            body = read[p]
        elif kinds[p] == "scan":
            head = (f"<!-- page {p} of {total}: NO TEXT LAYER (a scanned image) — its text "
                    "is not transcribed here; --ocr reads it -->")
            body = str(chunk.get("text") or "")      # what little the page does carry
        else:
            head, body = f"<!-- page {p} of {total} -->", str(chunk.get("text") or "")
        block = _lines(head + "\n\n" + body.strip()) + "\n\n"
        page_lines.append([p, line])
        line += block.count("\n")
        parts.append(block)
    meta = {"format": "pdf", "pages_total": total, "pages": [first, last], "capped": capped,
            "next_page": last + 1 if capped else None, "no_text_layer": scans,
            "blank": [p for p in window if kinds[p] == "blank"], "page_lines": page_lines}
    if ocr_report is not None:
        meta["ocr"] = ocr_report
    return "".join(parts), meta


def convert_office(path: Path, kind: str) -> tuple[str, dict]:
    size = path.stat().st_size
    if size > OFFICE_MAX_BYTES:
        raise ValueError(f"{size:,} bytes is over the {OFFICE_MAX_BYTES:,}-byte cap for an "
                         "Office document — its converter holds the whole file in memory; "
                         "export the part you need (a sheet as CSV, a PDF of the pages)")
    from markitdown import MarkItDown

    with contextlib.redirect_stdout(sys.stderr):
        result = MarkItDown(enable_plugins=False).convert_local(str(path),
                                                                file_extension=f".{kind}")
    return _lines(result.markdown or "") + "\n", {"format": kind}


def _lines(text: str) -> str:
    """One line break convention, so a summary's line numbers are the ones a reader counts."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run(path: Path, spec: str | None = None, max_pages: int = MAX_PAGES,
        want_ocr: bool = False) -> tuple[str, dict]:
    """(markdown, summary) for one document. Raises ValueError for what cannot be read."""
    deadline = _deadline()
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    kind = detect(path)
    if kind is None:
        raise ValueError(f"{path.name} is not a PDF or an Office (docx/pptx/xlsx) document")
    if max_pages < 1:
        raise ValueError("--max-pages must be at least 1")
    try:
        text, meta = (convert_pdf(path, spec, max_pages, want_ocr, deadline) if kind == "pdf"
                      else convert_office(path, kind))
    except ValueError:
        raise
    except Exception as exc:   # a converter's own failure: a damaged file, an unknown part
        raise ValueError(f"could not convert {path.name}: {type(exc).__name__}: {exc}") from exc
    return text, {"source": str(path), **meta, "chars": len(text)}


def write_atomic(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, target)


def _summary_line(meta: dict) -> str:
    if meta["format"] != "pdf":
        return f"[{meta['format']} → {meta['chars']} chars of Markdown]"
    first, last = meta["pages"]
    out = f"[pdf pages {first}-{last} of {meta['pages_total']} → {meta['chars']} chars]"
    if meta["capped"]:
        out += f" capped at --max-pages — the next page is {meta['next_page']}"
    if meta["no_text_layer"] and "ocr" not in meta:
        out += f" no text layer on page(s) {meta['no_text_layer']} — --ocr reads them"
    return out


def _fixtures(d: Path) -> dict[str, Path]:
    import pymupdf

    pdf = pymupdf.open()
    p1 = pdf.new_page()
    p1.insert_htmlbox(pymupdf.Rect(50, 50, 545, 400),
                      "<h1>Opening chapter</h1><p>The opening words of the selftest.</p>")
    p2 = pdf.new_page()          # a "scan": a page-sized image and a stamped page number
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    p2.insert_image(p2.rect, pixmap=pix)
    p2.insert_text((280, 830), "p. 2")
    pdf.new_page()                          # blank
    pdf.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 545, 400), "<p>Closing words.</p>")
    pdf.save(str(d / "four.pdf"))
    docx = d / "memo.docx"
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/'
            '2006/content-types"><Default Extension="rels" ContentType="application/vnd.'
            'openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType='
            '"application/xml"/><Override PartName="/word/document.xml" ContentType="'
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"'
            '/></Types>'))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>'))
        zf.writestr("word/document.xml", (
            f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>'
            'Memo body paragraph</w:t></w:r></w:p></w:body></w:document>'))
    from openpyxl import Workbook
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Quarterly deck title"
    deck.save(str(d / "deck.pptx"))
    book = Workbook()
    book.active.append(["region", "revenue"])
    book.active.append(["north", 4711])
    book.save(str(d / "sheet.xlsx"))
    with zipfile.ZipFile(d / "plain.zip", "w") as zf:
        zf.writestr("readme.txt", "not a document")
    (d / "notes.md").write_text("A note that mentions %PDF-1.7 headers.\n", encoding="utf-8")
    return {"pdf": d / "four.pdf", "docx": docx, "pptx": d / "deck.pptx",
            "xlsx": d / "sheet.xlsx", "zip": d / "plain.zip", "md": d / "notes.md"}


def selftest() -> int:
    global ask_vision, OFFICE_MAX_BYTES   # the OCR seam and the cap are swapped below
    with tempfile.TemporaryDirectory() as tmp:
        f = _fixtures(Path(tmp))
        assert [detect(f[k]) for k in ("pdf", "docx", "pptx", "xlsx", "zip", "md")] == \
            ["pdf", "docx", "pptx", "xlsx", None, None]
        # the PDF: page markers, the text, a scan reported and a blank page told apart
        text, meta = run(f["pdf"])
        assert meta["pages"] == [1, 4] and meta["pages_total"] == 4 and not meta["capped"], meta
        assert meta["no_text_layer"] == [2] and meta["blank"] == [3], meta
        assert "Opening chapter" in text and "Closing words" in text, text
        assert "NO TEXT LAYER (a scanned image)" in text and "ocr" not in meta, text
        assert "p. 2" in text, "a scan keeps the little text its layer holds"
        lines = text.split("\n")
        for p, ln in meta["page_lines"]:
            assert lines[ln - 1].startswith(f"<!-- page {p} of 4"), (p, ln, lines[ln - 1])
        # the window and the cap
        _t, one = run(f["pdf"], "4")
        assert one["pages"] == [4, 4] and one["page_lines"] == [[4, 1]], one
        _t, cut = run(f["pdf"], None, max_pages=2)
        assert cut["pages"] == [1, 2] and cut["capped"] and cut["next_page"] == 3, cut
        assert page_window("40-", 50, 200) == (40, 50, False)
        assert page_window("-5", 50, 200) == (1, 5, False)
        for bad in ("7-3", "x", "60"):
            try:
                page_window(bad, 50, 200)
                raise AssertionError(f"{bad!r} must be refused")
            except ValueError:
                pass
        # --ocr: ONLY the no-text page goes to vision, and its text is marked as OCR
        asked: list[str] = []
        real = ask_vision

        def fake(png, left):
            asked.append(png.name)
            return "Scanned page words"

        ask_vision = fake
        try:
            text, meta = run(f["pdf"], want_ocr=True)
        finally:
            ask_vision = real
        assert asked == ["page-2.png"], asked
        assert meta["ocr"] == {"done": [2], "failed": {}, "skipped": []}, meta
        assert "read by OCR" in text and "Scanned page words" in text, text

        def keyless(png, left):
            raise RuntimeError("error: OPENROUTER_VISION_KEY is not set")

        ask_vision = keyless
        try:
            text, meta = run(f["pdf"], want_ocr=True)
        finally:
            ask_vision = real
        assert "OPENROUTER_VISION_KEY" in meta["ocr"]["failed"]["2"], meta
        assert "NO TEXT LAYER (a scanned image)" in text, text
        # past the call's deadline a page is skipped, never started — and named
        import pymupdf

        ask_vision = fake
        try:
            texts, report = ocr(pymupdf.open(str(f["pdf"])), [2], time.monotonic())
        finally:
            ask_vision = real
        assert texts == {} and report["skipped"] == [2], report
        # the Office formats
        assert "Memo body paragraph" in run(f["docx"])[0]
        assert "Quarterly deck title" in run(f["pptx"])[0]
        xlsx_text, xlsx_meta = run(f["xlsx"])
        assert "4711" in xlsx_text and xlsx_meta["format"] == "xlsx", xlsx_text
        # refusals: not a document, an Office file over the cap
        for k in ("zip", "md"):
            try:
                run(f[k])
                raise AssertionError(f"{k} must be refused")
            except ValueError as exc:
                assert "not a PDF or an Office" in str(exc)
        cap, OFFICE_MAX_BYTES = OFFICE_MAX_BYTES, 10
        try:
            run(f["docx"])
            raise AssertionError("an Office file over the cap must be refused")
        except ValueError as exc:
            assert "cap for an Office document" in str(exc)
        finally:
            OFFICE_MAX_BYTES = cap
        # --out lands whole
        out = Path(tmp) / "out.md"
        write_atomic(out, "x\n")
        assert out.read_text(encoding="utf-8") == "x\n"
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu doc-read",
                                description="Read a PDF / docx / pptx / xlsx as Markdown.")
    p.add_argument("file", nargs="?", help="the document")
    p.add_argument("--pages", default=None, help="PDF page window, 1-based: 3-7, 12, 40-, -5")
    p.add_argument("--max-pages", type=int, default=MAX_PAGES,
                   help=f"pages converted per call (default {MAX_PAGES})")
    p.add_argument("--ocr", action="store_true",
                   help="read pages with no text layer through the vision util (cloud, billed)")
    p.add_argument("--out", default=None, help="write the Markdown here instead of stdout")
    p.add_argument("--json", action="store_true", help="a JSON summary on stdout")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.file:
        p.print_usage(sys.stderr)
        return 2
    try:
        text, meta = run(Path(args.file), args.pages, args.max_pages, args.ocr)
        if args.out:
            write_atomic(Path(args.out), text)
            meta["out"] = args.out
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(meta if args.out else {**meta, "markdown": text}, ensure_ascii=False))
    else:
        if not args.out:
            sys.stdout.write(text)
        print(_summary_line(meta), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
