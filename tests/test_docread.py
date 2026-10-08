"""read_file meets a DOCUMENT (engine/docread.py): a PDF or an Office file is converted to
Markdown by the `doc-read` util and paged like a text file, instead of refused as a binary.

Pinned here: what makes a file a document (its magic bytes — and the util's own copy of that
test agrees), the content-hash cache (a re-read converts nothing, a changed file converts
again, nothing failed is kept), paging over the converted text, the fallback to the binary
refusal with what failed, the observation's wording, the grounding a converted read gives,
the engine-owned cache, and the one engine call — offline although the util's tree is not.
The last test runs the REAL util through the real runner on a hand-made PDF.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import run_context, server_config
from rsched import sandbox, utils_lib, utils_run
from rsched.engine import docread, fileops
from rsched.engine.history import seen_paths
from rsched.engine.obs_files import format_files

REPO = Path(__file__).resolve().parents[1]
UTIL = REPO / "util-seed" / "utils" / "doc-read" / "main.py"


def _pdf(path: Path, texts: list[str]) -> Path:
    """A minimal real PDF, one page per entry (an empty entry is a blank page). No NUL byte
    anywhere — the shape read_file used to decode as text."""
    n = len(texts)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(texts):
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                    f"<< /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n"
            "%%EOF\n").encode()
    path.write_bytes(out)
    return path


def _zip(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "<x/>")
    return path


def _fixtures(d: Path) -> dict[str, tuple[Path, str | None]]:
    """name → (file, the kind both detectors must name)."""
    (d / "junk.pdf").write_bytes(b"garbage first\n%PDF-1.4\n")
    (d / "notes.md").write_text("A note about %PDF-1.7 headers.\n", encoding="utf-8")
    (d / "fake.docx").write_bytes(b"PK\x03\x04 not really a zip")
    return {
        "pdf": (_pdf(d / "a.pdf", ["Hello"]), "pdf"),
        "docx": (_zip(d / "a.docx", ["[Content_Types].xml", "word/document.xml"]), "docx"),
        "pptx": (_zip(d / "a.pptx", ["[Content_Types].xml", "ppt/slides/slide1.xml"]), "pptx"),
        "xlsx": (_zip(d / "a.xlsx", ["[Content_Types].xml", "xl/workbook.xml"]), "xlsx"),
        # by its bytes, never its name: a renamed workbook is still a workbook
        "renamed": (_zip(d / "report.bin", ["[Content_Types].xml", "xl/workbook.xml"]), "xlsx"),
        "plain-zip": (_zip(d / "a.zip", ["readme.txt"]), None),
        "odt-like": (_zip(d / "a.odt", ["[Content_Types].xml", "content.xml"]), None),
        "not-a-zip": (d / "fake.docx", None),
        "junk-prefix": (d / "junk.pdf", None),
        "text": (d / "notes.md", None),
    }


def _head(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(fileops.BINARY_SNIFF_BYTES)


def test_a_document_is_known_by_its_magic_bytes(tmp_path):
    for name, (path, kind) in _fixtures(tmp_path).items():
        assert docread.document_kind(path, _head(path)) == kind, name


def test_the_util_and_the_engine_agree_on_what_a_document_is(tmp_path):
    """The util cannot import the engine, so it carries its own detector: the two must name
    the same kind for the same bytes, or read_file sends the util a file it then refuses."""
    spec = importlib.util.spec_from_file_location("doc_read_util", UTIL)
    assert spec is not None and spec.loader is not None
    util = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(util)
    for name, (path, kind) in _fixtures(tmp_path).items():
        assert util.detect(path) == kind, name
    assert util.MAX_PAGES == docread.MAX_PAGES


# ---- the conversion seam, with the util faked --------------------------------------------------


def _ctx(tmp_path):
    return SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, fs_read_roots=[]),
                           grants=None, depth=0, seen_paths=set(), read_roots=list,
                           write_roots=list,
                           server=SimpleNamespace(routines_home=tmp_path / "routines",
                                                  libraries_home=tmp_path / "libraries"))


PAGES = 3
LINES_PER_PAGE = 100


def _markdown() -> tuple[str, list[list[int]]]:
    lines, page_lines = [], []
    for p in range(1, PAGES + 1):
        page_lines.append([p, len(lines) + 1])
        lines.append(f"<!-- page {p} of {PAGES} -->")
        lines += [f"p{p} line {i}" for i in range(1, LINES_PER_PAGE)]
    return "\n".join(lines) + "\n", page_lines


@pytest.fixture
def converter(monkeypatch):
    """The util, faked at its one seam: writes the Markdown to `--out` and prints the summary,
    as `doc-read --out PATH --json` does. `calls` counts conversions; `fail` makes the next
    one fail like a util that exits non-zero."""
    state = SimpleNamespace(calls=[], fail=None, meta={})
    text, page_lines = _markdown()

    def fake(ctx, src, out):
        state.calls.append(src)
        if state.fail:
            return state.fail
        out.write_text(text, encoding="utf-8")
        meta = {"source": str(src), "format": "pdf", "pages_total": PAGES,
                "pages": [1, PAGES], "capped": False, "next_page": None,
                "no_text_layer": [2], "blank": [], "page_lines": page_lines,
                "chars": len(text), "out": str(out), **state.meta}
        return 0, json.dumps(meta), ""

    monkeypatch.setattr(docread, "_run_util", fake)
    monkeypatch.setattr(docread.utils_lib, "exists", lambda home, name: name == "doc-read")
    return state


def test_a_converted_pdf_pages_like_a_text_file(tmp_path, converter):
    _pdf(tmp_path / "report.pdf", ["Hello", "", "World"])
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("report.pdf", {"start_line": 95, "max_lines": 10}, ctx)
    assert obs["total_lines"] == PAGES * LINES_PER_PAGE
    assert obs["content"].splitlines() == [
        *(f"p1 line {i}" for i in range(94, 100)), "<!-- page 2 of 3 -->",
        *(f"p2 line {i}" for i in range(1, 4))]
    assert obs["converted"] == {"from": "pdf", "pages": [1, 3], "pages_total": 3,
                                "capped": False, "no_text_layer": [2], "window_pages": [1, 2]}
    assert str(tmp_path / "report.pdf") in ctx.seen_paths      # a converted read grounds
    # start_char enters a line part-way, on the CONVERTED text
    part = fileops._read_one("report.pdf", {"start_line": 2, "start_char": 3, "max_lines": 1},
                             ctx)
    assert part["content"] == "line 1"


def test_the_conversion_is_cached_by_content_hash(tmp_path, converter):
    pdf = _pdf(tmp_path / "a.pdf", ["Hello"])
    ctx = _ctx(tmp_path)
    fileops._read_one("a.pdf", {}, ctx)
    fileops._read_one("a.pdf", {"start_line": 201}, ctx)          # the next window: a hit
    assert len(converter.calls) == 1
    shutil.copy(pdf, tmp_path / "copy.pdf")                       # same bytes, another name
    fileops._read_one("copy.pdf", {}, ctx)
    assert len(converter.calls) == 1
    _pdf(pdf, ["Hello, changed"])                                 # new bytes, a new key
    fileops._read_one("a.pdf", {}, ctx)
    assert len(converter.calls) == 2
    cache = tmp_path / docread.CACHE_DIR
    assert sorted(p.suffix for p in cache.iterdir()) == [".json", ".json", ".md", ".md"]
    assert ".doc_cache/" in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_a_document_over_the_read_cap_is_converted_not_refused(tmp_path, converter,
                                                               monkeypatch):
    monkeypatch.setattr(fileops, "READ_MAX_BYTES", 10)
    _pdf(tmp_path / "big.pdf", ["Hello"])
    obs = fileops._read_one("big.pdf", {}, _ctx(tmp_path))
    assert "error" not in obs and obs["content"].startswith("<!-- page 1 of 3 -->")


@pytest.mark.parametrize(("result", "said"), [
    ((1, "", "error: could not convert a.pdf: FileDataError: broken xref"),
     "the doc-read util failed (exit 1): error: could not convert a.pdf: FileDataError"),
    ((124, "", "util 'doc-read' timed out after 300s (process group terminated)"),
     "the doc-read util failed (exit 124): util 'doc-read' timed out after 300s"),
    ((0, "not json", ""), "the doc-read util returned no summary: not json"),
])
def test_a_failed_conversion_falls_back_to_the_binary_refusal(tmp_path, converter, result,
                                                              said):
    pdf = _pdf(tmp_path / "a.pdf", ["Hello"])
    converter.fail = result
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("a.pdf", {}, ctx)
    size = pdf.stat().st_size
    assert obs["size"] == size and "content" not in obs
    assert obs["error"].startswith(f"binary file ({size:,} bytes) — read_file shows text only")
    assert "It is a PDF, which read_file converts to Markdown, and that failed: " in obs["error"]
    assert said in obs["error"]
    assert str(pdf) in ctx.seen_paths                      # the stat grounds, as any refusal
    assert not list((tmp_path / docread.CACHE_DIR).glob("*.md"))   # nothing failed is kept
    converter.fail = None
    assert "content" in fileops._read_one("a.pdf", {}, ctx)  # …so the next read tries again


def test_a_missing_util_is_said_and_nothing_runs(tmp_path, converter, monkeypatch):
    monkeypatch.setattr(docread.utils_lib, "exists", lambda home, name: False)
    _pdf(tmp_path / "a.pdf", ["Hello"])
    obs = fileops._read_one("a.pdf", {}, _ctx(tmp_path))
    assert "the `doc-read` util that converts a PDF is not installed" in obs["error"]
    assert converter.calls == []


def test_the_cache_keeps_the_most_recently_read(tmp_path, converter, monkeypatch):
    monkeypatch.setattr(docread, "KEEP_DOCS", 2)
    ctx = _ctx(tmp_path)
    for i in range(3):
        _pdf(tmp_path / f"d{i}.pdf", [f"doc {i}"])
        fileops._read_one(f"d{i}.pdf", {}, ctx)
    assert len(list((tmp_path / docread.CACHE_DIR).glob("*.md"))) == 2
    fileops._read_one("d0.pdf", {}, ctx)          # pruned: converted again
    assert len(converter.calls) == 4


# ---- the observation ---------------------------------------------------------------------------


def test_the_observation_says_it_is_a_conversion(tmp_path, converter):
    _pdf(tmp_path / "report.pdf", ["Hello"])
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("report.pdf", {"max_lines": 150}, ctx)
    head = format_files({"kind": "read_file", **obs}, "read_file").splitlines()[0]
    assert head == (
        "OBSERVATION (read_file report.pdf — converted from a PDF to Markdown (layout "
        "approximated), pages 1-3 of 3, this window pages 1-2; 1 page(s) have NO TEXT LAYER "
        "(scanned images, not transcribed): 2 — view_image sees them, the doc-read util's "
        "--ocr transcribes them; lines 1-150 of 300):")
    batched = format_files({"kind": "read_file", "files": [obs]}, "read_file")
    assert "--- report.pdf (converted from a PDF to Markdown (layout approximated), " in batched
    assert "; lines 1-150 of 300) ---" in batched


def test_a_capped_conversion_names_the_rest_and_how_to_reach_it():
    said = docread.clause({"from": "pdf", "pages": [1, 200], "pages_total": 540,
                           "capped": True, "no_text_layer": [*range(5, 9), 12, 40]})
    assert ("pages 201-540 NOT converted — one read converts at most 200 pages; the doc-read "
            "util converts any window (--pages 201-400 --out <file>, then read_file that "
            "file)") in said
    assert "6 page(s) have NO TEXT LAYER (scanned images, not transcribed): 5-8, 12, 40" in said
    assert docread.clause({"from": "xlsx"}) == ("converted from an Excel workbook to Markdown "
                                                "(layout approximated)")


def test_the_window_pages_follow_the_line_window():
    page_lines = [[1, 1], [2, 40], [3, 90]]
    assert docread._window_pages(page_lines, 1, 39) == [1, 1]
    assert docread._window_pages(page_lines, 39, 40) == [1, 2]
    assert docread._window_pages(page_lines, 50, 200) == [2, 3]
    assert docread._window_pages(page_lines, 300, 299) is None     # a window past the end
    assert docread._ranges(list(range(1, 40, 2)), limit=3) == "1, 3, 5, … (17 more runs)"


def test_a_converted_read_grounds_on_resume_too(tmp_path, converter):
    _pdf(tmp_path / "a.pdf", ["Hello"])
    obs = fileops.do_read_file({"kind": "read_file", "path": "a.pdf"}, _ctx(tmp_path))
    assert seen_paths([{"type": "observation", "payload": obs}]) == ["a.pdf"]


# ---- the cache is the engine's -----------------------------------------------------------------


def test_the_cache_is_engine_owned(make_routine, tmp_path):
    from rsched.grantpolicy import GrantPolicy

    d = make_routine()
    ctx = run_context(d, "20261008-070000", server=server_config(
        libraries_home=tmp_path / "libraries"), grants=GrantPolicy())
    obs = fileops.do_write_file({"kind": "write_file", "path": ".doc_cache/x.md",
                                 "content": "forged"}, ctx)
    assert ".doc_cache/ is engine-owned and read-only for the run" in obs["error"]


# ---- the one engine call: offline, secret-free ------------------------------------------------


CALLER = '''"""caller — calls an outbound sibling only under a flag.
usage: gu caller [--net] [--json] [--selftest]
calls: pinger
tags: test
secrets: PING_KEY?
net: none
fs: roots
"""
'''
PINGER = '''"""pinger — needs the network.
usage: gu pinger [--json] [--selftest]
calls: (none)
tags: test
secrets: PING_KEY
net: outbound
fs: none
"""
'''


def test_offline_narrows_a_tree_the_engine_will_not_take_online(tmp_path, monkeypatch):
    home = tmp_path / "libraries"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "caller", CALLER)
    utils_lib.write_util_file(home, "pinger", PINGER)
    wrapped, prewarmed = [], []
    monkeypatch.setattr(utils_run.shutil, "which", lambda _b: "/usr/bin/uv")
    monkeypatch.setattr(utils_run, "prewarm_script_deps",
                        lambda script, policy, _home, **_kw: prewarmed.append(script))
    monkeypatch.setattr(utils_run.sandbox, "wrap",
                        lambda cmd, **kw: wrapped.append(kw["net"]) or cmd)
    monkeypatch.setattr(utils_run, "run_jailed", lambda *a, **k: utils_run.Jailed(
        0, utils_run.CapturedOutput(""), utils_run.CapturedOutput(""), False))
    off = sandbox.SandboxPolicy(mode="off")
    utils_run.run_util(home, "caller", [], policy=off)
    assert wrapped == [True] and prewarmed == []          # the tree is outbound…
    utils_run.run_util(home, "caller", [], policy=off, offline=True)
    assert wrapped == [True, False] and len(prewarmed) == 1  # …unless the call narrows it


def test_the_engine_call_is_offline_and_hands_over_no_secret(tmp_path, monkeypatch):
    seen = {}

    def fake_run_util(home, name, args, **kw):
        seen.update(name=name, args=args, **kw)
        return 0, "", ""

    monkeypatch.setattr(docread.utils_run, "run_util", fake_run_util)
    monkeypatch.setattr(docread.utils_run, "util_needs",
                        lambda home, name: utils_run.UtilNeeds({"OPENROUTER_VISION_KEY"}, True,
                                                               {"OPENROUTER_VISION_KEY"}, True,
                                                               ()))
    monkeypatch.setattr(docread.sandbox, "policy_for_ctx", lambda ctx: "the run's policy")
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path), aborted=lambda: False,
                          server=SimpleNamespace(libraries_home=tmp_path))
    docread._run_util(ctx, tmp_path / "a.pdf", tmp_path / "out.part")
    assert seen["name"] == "doc-read" and seen["offline"] is True
    assert seen["withhold_secrets"] == {"OPENROUTER_VISION_KEY"}
    assert seen["policy"] == "the run's policy" and "--ocr" not in seen["args"]
    assert seen["args"][1:] == ["--out", str(tmp_path / "out.part"), "--max-pages", "200",
                                "--json"]


# ---- end to end: the real util, the real runner -----------------------------------------------


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv runs the util")
def test_the_real_util_converts_a_pdf_through_the_engine(make_routine, tmp_path, monkeypatch):
    home = tmp_path / "libraries"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "doc-read", UTIL.read_text(encoding="utf-8"))
    d = make_routine()
    _pdf(d / "contract.pdf", ["Clause one applies.", ""])
    ctx = run_context(d, "20261008-070000", server=server_config(libraries_home=home))
    obs = fileops._read_one("contract.pdf", {}, ctx)
    assert "error" not in obs, obs
    assert "Clause one applies." in obs["content"]
    assert obs["content"].startswith("<!-- page 1 of 2 -->")
    assert obs["converted"]["pages"] == [1, 2] and obs["converted"]["no_text_layer"] == []
    # the second read is served from the cache: the util is not run again
    monkeypatch.setattr(docread, "_run_util", lambda *a: pytest.fail("converted twice"))
    assert fileops._read_one("contract.pdf", {}, ctx)["content"] == obs["content"]
