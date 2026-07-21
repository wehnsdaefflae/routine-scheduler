"""Help-tab content builder: pdoc API reference + rendered markdown guides.

The console's Help tab serves static HTML generated from THIS source tree. pdoc renders
the `rsched` package's docstrings — they are plain markdown prose, pdoc's native format —
into fully self-contained pages (inline CSS/JS, offline search, no CDNs, matching the
frontend's no-external-assets standard). The hand-written guides in `docs/*.md` are
rendered through markdown2 (already present as a pdoc dependency) into a dark shell that
matches the console. Output lands OUTSIDE the source repo (`~/.cache/routine-scheduler/docs`)
and the web app mounts it at `/docs`; `ensure_docs` is called at daemon boot in a thread and
skips the build when the source stamp is unchanged, so a restart onto unchanged code costs
one directory scan.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .paths import atomic_write, atomic_write_json, expand

log = logging.getLogger("rsched.docs")

STAMP_FILE = ".stamp"
# the console's favicon (index.html), so the iframe'd pages carry the same mark
FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           "<circle cx='50' cy='50' r='34' fill='none' stroke='%23ffb454' stroke-width='8'/>"
           "<circle cx='50' cy='16' r='9' fill='%23ffb454'/></svg>")

# Dark shell for guide pages — mirrors static/base.css ("signal deck") tokens so a guide
# reads as part of the console even though it renders inside an iframe.
GUIDE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 32px 60px; background: #0a0e13; color: #d5dee6;
  font: 15px/1.65 system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  max-width: 860px; }
h1, h2, h3 { color: #ffc87d; line-height: 1.25; }
h1 { font-size: 26px; } h2 { margin-top: 34px; } h3 { margin-top: 26px; }
a { color: #45e0b0; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
  background: #141d28; border: 1px solid #1e2a37; border-radius: 4px; padding: 1px 5px; }
pre { background: #101720; border: 1px solid #1e2a37; border-radius: 8px;
  padding: 12px 14px; overflow-x: auto; }
pre code { background: none; border: none; padding: 0; }
table { border-collapse: collapse; margin: 12px 0; display: block; overflow-x: auto; }
th, td { border: 1px solid #1e2a37; padding: 6px 12px; text-align: left; }
th { background: #141d28; color: #ffc87d; }
blockquote { border-left: 3px solid #ffb454; margin-left: 0; padding-left: 14px;
  color: #8598a9; }
hr { border: none; border-top: 1px solid #1e2a37; }
"""


# Dark pdoc theme in the console's palette — pdoc's own variables, our colors. Without
# this the API reference renders in pdoc's white default and looks like a foreign site
# inside the Help tab's iframe.
PDOC_THEME_CSS = """
/* signal-deck dark theme for pdoc (overrides templates/theme.css) */
:root { --pdoc-background: #0a0e13; color-scheme: dark; }
.pdoc {
    --text: #d5dee6;
    --muted: #8598a9;
    --link: #45e0b0;
    --link-hover: #7deec9;
    --code: #141d28;
    --active: #2a2416;

    --accent: #141d28;
    --accent2: #1e2a37;

    --nav-hover: rgba(255, 180, 84, 0.08);
    --name: #ffb454;
    --def: #45e0b0;
    --annotation: #8598a9;
}
.pdoc h1, .pdoc h2, .pdoc h3, .pdoc h4 { color: #ffc87d; }
.pdoc pre { border: 1px solid #1e2a37; border-radius: 8px; }
.pdoc .docstring code, .pdoc summary code { border: 1px solid #1e2a37; border-radius: 4px; }
input[type="search"] { background: #141d28; color: #d5dee6; border: 1px solid #1e2a37; }
"""

# The Help tab's reading order: orientation first, worked examples second, then the
# deeper contract docs. Guides not named here sort alphabetically after them.
GUIDE_ORDER = ["getting-started", "examples", "conversations", "playbooks",
               "traits-permissions", "curated-traits", "notifications", "subtasks",
               "background-tasks", "triggers", "run-analytics", "authoring", "sandboxing",
               "prompt-anatomy", "endpoints"]


def docs_out_dir() -> Path:
    """Where generated docs live — outside the repo, so builds never dirty the source tree."""
    import os

    return expand(os.environ.get("RSCHED_DOCS_DIR") or "~/.cache/routine-scheduler/docs")


def source_stamp(source_repo: Path) -> str:
    """Cheap staleness key: version + newest mtime across the doc inputs. Mtime (not git
    HEAD) so uncommitted docstring edits rebuild too — the scan is ~70 files.
    """
    from . import __version__

    newest = 0
    for pattern, root in (("src/rsched/**/*.py", source_repo), ("docs/*.md", source_repo)):
        for p in root.glob(pattern):
            newest = max(newest, int(p.stat().st_mtime))
    return f"{__version__}:{newest}"


def guide_title(text: str, slug: str) -> str:
    """A guide's display title = its first markdown heading (fallback: the file's slug)."""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else slug


def render_guide(text: str, title: str) -> str:
    """One hand-written markdown guide → a self-contained dark HTML page."""
    import markdown2

    body = markdown2.markdown(
        text, extras=["fenced-code-blocks", "tables", "header-ids", "strike"])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><link rel="icon" href="{FAVICON}">'
            f"<style>{GUIDE_CSS}</style></head><body>{body}</body></html>")


def build_docs(source_repo: Path, out: Path, *, modules: tuple[str, ...] = ("rsched",),
               force: bool = False) -> bool:
    """Generate guides + API reference into `out`. Returns False when the stamp says the
    source is unchanged (the cheap path a no-op restart takes). The stamp is written LAST,
    so a failed build retries on the next boot.
    """
    stamp = source_stamp(source_repo)
    marker = out / STAMP_FILE
    if not force and marker.is_file() and marker.read_text(encoding="utf-8") == stamp:
        return False
    out.mkdir(parents=True, exist_ok=True)

    guides = []
    order = {slug: n for n, slug in enumerate(GUIDE_ORDER)}
    docs_md = sorted((source_repo / "docs").glob("*.md"),
                     key=lambda p: (order.get(p.stem, len(order)), p.stem))
    for md in docs_md:
        text = md.read_text(encoding="utf-8")
        title = guide_title(text, md.stem)
        atomic_write(out / "guides" / f"{md.stem}.html", render_guide(text, title))
        guides.append({"slug": md.stem, "title": title})

    import pdoc
    import pdoc.render

    template_dir = out / "_pdoc-template"
    atomic_write(template_dir / "theme.css", PDOC_THEME_CSS)
    pdoc.render.configure(favicon=FAVICON, template_directory=template_dir,
                          footer_text="rsched — generated from source by pdoc")
    pdoc.pdoc(*modules, output_directory=out / "api")

    from . import __version__

    atomic_write_json(out / "index.json", {
        "version": __version__, "stamp": stamp, "guides": guides,
        "api": f"api/{modules[0]}.html"})
    atomic_write(marker, stamp)
    return True


def ensure_docs(source_repo: Path) -> None:
    """Boot-time entry: build if stale, never raise (docs must not take the daemon down)."""
    # Test/ops opt-out (RSCHED_NO_SCHEDULER's sibling): without it every TestClient(app)
    # pays a pdoc build — the lifespan's to_thread task cannot be cancelled, only awaited.
    if os.environ.get("RSCHED_SKIP_DOCS_BUILD"):
        return
    try:
        if build_docs(source_repo, docs_out_dir()):
            log.info("docs: rebuilt into %s", docs_out_dir())
    except Exception as exc:
        log.warning("docs build failed (Help tab shows last good build): %s", exc)
