"""docs_build: guide rendering, staleness stamp, and the full (scoped) build."""

from pathlib import Path

from rsched.docs_build import build_docs, ensure_docs, guide_title, render_guide, source_stamp


def _src_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "srcrepo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "endpoints.md").write_text(
        "# LLM endpoint setup\n\nAdd one in `Settings`.\n\n```yaml\nkind: openai\n```\n",
        encoding="utf-8")
    (repo / "src" / "rsched").mkdir(parents=True)
    (repo / "src" / "rsched" / "x.py").write_text("A = 1\n", encoding="utf-8")
    return repo


def test_guide_title_prefers_first_heading():
    assert guide_title("intro\n\n# Real Title\n## sub", "slug") == "Real Title"
    assert guide_title("no headings here", "fallback-slug") == "fallback-slug"


def test_render_guide_is_selfcontained_html():
    html = render_guide("# T\n\nsome `code` and\n\n```python\nx = 1\n```\n", "T")
    assert html.startswith("<!doctype html>")
    assert "<h1" in html and "<pre" in html and "<code" in html
    assert "<title>T</title>" in html
    assert "http://" not in html.split("</style>")[1]  # body: no external asset fetches


def test_build_skip_and_force(tmp_path):
    repo = _src_repo(tmp_path)
    out = tmp_path / "out"
    # scoped to one tiny module so the test stays fast; layout matches the real build
    assert build_docs(repo, out, modules=("rsched.ids",)) is True
    assert (out / "guides" / "endpoints.html").is_file()
    assert (out / "api" / "rsched" / "ids.html").is_file()
    index = (out / "index.json").read_text(encoding="utf-8")
    assert '"endpoints"' in index and "LLM endpoint setup" in index
    # unchanged source → the stamp short-circuits; force rebuilds anyway
    assert build_docs(repo, out, modules=("rsched.ids",)) is False
    assert build_docs(repo, out, modules=("rsched.ids",), force=True) is True
    # touching a doc input invalidates the stamp
    py = repo / "src" / "rsched" / "x.py"
    import os
    st = py.stat()
    os.utime(py, (st.st_atime + 5, st.st_mtime + 5))
    assert source_stamp(repo) != (out / ".stamp").read_text(encoding="utf-8")


def test_ensure_docs_never_raises(tmp_path, monkeypatch):
    # a failing build (import error, unreadable repo, …) must not take the daemon down
    import rsched.docs_build as db

    def boom(*a, **kw):
        raise RuntimeError("pdoc exploded")

    monkeypatch.setenv("RSCHED_DOCS_DIR", str(tmp_path / "docs-out"))
    monkeypatch.delenv("RSCHED_SKIP_DOCS_BUILD", raising=False)   # conftest sets it suite-wide
    monkeypatch.setattr(db, "build_docs", boom)
    ensure_docs(tmp_path / "missing")


def test_ensure_docs_honors_skip_env(tmp_path, monkeypatch):
    """RSCHED_SKIP_DOCS_BUILD short-circuits ensure_docs BEFORE build_docs - the knob the
    whole test suite (and ops) relies on to keep TestClient(app) from paying a pdoc build."""
    from rsched import docs_build

    calls = []
    monkeypatch.setattr(docs_build, "build_docs", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setenv("RSCHED_SKIP_DOCS_BUILD", "1")
    docs_build.ensure_docs(tmp_path)
    assert not calls, "skip env set - build_docs must not run"

    monkeypatch.delenv("RSCHED_SKIP_DOCS_BUILD")
    docs_build.ensure_docs(tmp_path)
    assert len(calls) == 1, "without the env the boot path builds"
