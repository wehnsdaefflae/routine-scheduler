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
           "<circle cx='50' cy='50' r='34' fill='none' stroke='%233fd8c2' stroke-width='8'/>"
           "<circle cx='50' cy='16' r='9' fill='%233fd8c2'/></svg>")

# A generated page is read INSIDE the console's own frame, so it follows the console's theme or
# it reads as a foreign site — which is what a hard-coded dark shell did on a light console, on
# the one page whose job is explaining the console. Both shells below therefore declare the
# palette once as `light-dark()` over `color-scheme: light dark`, exactly as base.css does, and
# the frame's `?theme=` parameter stamps `data-theme` for the two explicit choices.
THEME_SCRIPT = """
var t = new URLSearchParams(location.search).get('theme');
if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
"""

# The console's own tokens, restated for a standalone page (a generated file cannot import
# base.css — the output lands outside the source tree and is served from /docs).
THEME_TOKENS = """
:root { color-scheme: light dark; }
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"] { color-scheme: dark; }
:root {
  --deck:    light-dark(#f4f6f9, #0d1218);
  --plate:   light-dark(#ffffff, #151e29);
  --plate-2: light-dark(#f7f9fc, #1b2634);
  --rule:    light-dark(#dde3ec, #223044);
  --ink:     light-dark(#16202c, #e3e9f0);
  --ink-2:   light-dark(#4d5f74, #94a5b8);
  --ink-3:   light-dark(#5a6c80, #7a90a8);
  --signal:  light-dark(#0f9b8a, #3fd8c2);
  --iris:    light-dark(#6a55d0, #a99cf5);
  --f-display: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --f-mono: ui-monospace, "SF Mono", "SFMono-Regular", "Cascadia Mono", Menlo, Consolas, monospace;
  --f-prose: ui-serif, Georgia, "Iowan Old Style", Charter, "Times New Roman", serif;
}
"""

# The guide shell: a hand-written markdown guide rendered as part of the console.
GUIDE_CSS = THEME_TOKENS + """
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 32px 60px; background: var(--deck); color: var(--ink);
  font: 15px/1.65 var(--f-prose); max-width: 860px; }
h1, h2, h3 { color: var(--ink); font-family: var(--f-display); line-height: 1.25; }
h1 { font-size: 26px; } h2 { margin-top: 34px; } h3 { margin-top: 26px; }
a { color: var(--signal); }
code { font-family: var(--f-mono); font-size: 13px;
  background: var(--plate-2); border: 1px solid var(--rule); border-radius: 4px; padding: 1px 5px; }
pre { background: var(--plate); border: 1px solid var(--rule); border-radius: 8px;
  padding: 12px 14px; overflow-x: auto; }
pre code { background: none; border: none; padding: 0; }
table { border-collapse: collapse; margin: 12px 0; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--rule); padding: 6px 12px; text-align: left; }
th { background: var(--plate-2); font-family: var(--f-display); }
blockquote { border-left: 3px solid var(--iris); margin-left: 0; padding-left: 14px;
  color: var(--ink-2); }
hr { border: none; border-top: 1px solid var(--rule); }
"""


# pdoc in the console's palette — pdoc's own variables, our tokens. Without this the API
# reference renders in pdoc's default and looks like a foreign site inside the Help tab.
PDOC_THEME_CSS = THEME_TOKENS + """
/* watchfloor theme for pdoc (overrides templates/theme.css) — pdoc's own variables, our tokens */
:root { --pdoc-background: var(--deck); }
.pdoc {
    --text: var(--ink);
    --muted: var(--ink-2);
    --link: var(--signal);
    --link-hover: var(--signal);
    --code: var(--plate-2);
    --active: var(--plate-2);

    --accent: var(--plate-2);
    --accent2: var(--rule);

    --nav-hover: var(--plate-2);
    --name: var(--iris);
    --def: var(--signal);
    --annotation: var(--ink-2);
}
.pdoc h1, .pdoc h2, .pdoc h3, .pdoc h4 { color: var(--ink); font-family: var(--f-display); }
.pdoc pre { border: 1px solid var(--rule); border-radius: 8px; }
.pdoc .docstring code, .pdoc summary code { border: 1px solid var(--rule); border-radius: 4px; }
input[type="search"] { background: var(--plate-2); color: var(--ink);
  border: 1px solid var(--rule); }
"""

# The Help tab's reading order: orientation first, worked examples second, then the deeper
# contract docs. Guides not named here sort alphabetically after them, so a NEW guide reaches
# the tab whether or not anyone remembers this list — the list only decides where it reads well.
# A name here that no `docs/*.md` answers to costs nothing and says nothing, which is how
# `subtasks` outlived the guide it named; the guide is `child-runs`.
GUIDE_ORDER = ["getting-started", "examples", "authoring", "conversations", "playbooks",
               "child-runs", "background-tasks", "triggers", "schedule-once", "run-gates",
               "lanes-domains", "rules-permissions", "curated-rules", "rule-assists",
               "reminders", "items", "messages", "status-pages", "run-analytics", "search",
               "notifications", "sandboxing", "admin", "endpoints", "oauth-connections",
               "remote-machines", "browser-sessions", "darknet", "usenet",
               "output-compression", "revise-recipe", "claude-proxy-cutover",
               "prompt-anatomy", "architecture", "designs"]


def docs_out_dir() -> Path:
    """Where generated docs live — outside the repo, so builds never dirty the source tree."""
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
    """One hand-written markdown guide → a self-contained, theme-following HTML page.

    Self-contained is a hard requirement: the page is served from `/docs`, outside the source
    tree, so it can fetch nothing from `static/`. `?theme=light|dark` stamps `data-theme` for a
    reader who chose one; with no parameter the page follows the machine, which is what the
    console's own default does.
    """
    import markdown2

    body = markdown2.markdown(
        text, extras=["fenced-code-blocks", "tables", "header-ids", "strike"])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><link rel="icon" href="{FAVICON}">'
            f"<style>{GUIDE_CSS}</style><script>{THEME_SCRIPT}</script>"
            f"</head><body>{body}</body></html>")


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
