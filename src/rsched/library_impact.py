"""The REVERSE reading of the dependency graph: who depends on this, and does this break them?

`readmodels/surface.py` asks "what does this routine still need?". This asks the same join in
the other direction, and it exists because of how the library MOVES: there is exactly one copy
of every util and every rule, so a revision reaches every holder at its next run, with no
migration and nothing to review. That leverage is the design's best property. It is also why a
routine nobody touched can stop working overnight.

Two thirds of the machinery was already here and pointed at one question each. `write_rule`'s
approval computes `_rule_holders` and says "It binds: …" — WHO, never what it breaks.
`remove_util` refuses when `utils_lib.referenced_by()` is non-empty — util→util only, never
util→routine. This is the join between them.

The break analysis is deliberately NOT a per-kind diff of headers and frontmatter. It computes
each holder's setup surface against the CURRENT library and against the PROPOSED one, and
reports whoever gains a blocking or interrupting row. So the approval question and the routine
page can never disagree about what a gap means: it is one function, read forwards, twice.
"""

from __future__ import annotations

import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Which library subdirectory each writable kind lives in.
_KIND_DIR = {"util": "utils", "rule": "rules", "permission": "permissions"}


def _routine_dirs(server: Any) -> list[Path]:
    home = getattr(server, "routines_home", None)
    if not home or not Path(home).is_dir():
        return []
    return sorted(p for p in Path(home).iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "routine.yaml").is_file())


@contextmanager
def _shadow_library(real: Path, kind: str, name: str, content: str | None):
    """A library that is the real one with ONE document replaced (or removed).

    Built from symlinks — one per util directory and per doc — so shadowing a 140-util library
    costs a few milliseconds and no copying. The alternative was a per-kind differ that
    re-derived what a changed `secrets:` or `expects:` line means, which is knowledge the
    surface already holds and would be free to drift from.
    """
    with tempfile.TemporaryDirectory(prefix="rsched-impact-") as tmp:
        shadow = Path(tmp)
        for sub in ("utils", "permissions", "rules"):
            src = real / sub
            dst = shadow / sub
            dst.mkdir(parents=True, exist_ok=True)
            if not src.is_dir():
                continue
            for entry in src.iterdir():
                replacing = (sub == _KIND_DIR.get(kind)
                             and entry.name == (name if kind == "util" else f"{name}.md"))
                if not replacing:
                    (dst / entry.name).symlink_to(entry)
        if content is not None:
            target = shadow / _KIND_DIR[kind]
            if kind == "util":
                (target / name).mkdir(parents=True, exist_ok=True)
                (target / name / "main.py").write_text(content, encoding="utf-8")
            else:
                (target / f"{name}.md").write_text(content, encoding="utf-8")
        yield shadow


def _server_at(server: Any, libraries_home: Path) -> Any:
    """The same server, reading a different library. A shallow stand-in rather than a mutated
    copy: ServerConfig is a validated model and the surface only ever reads four attributes.
    """
    from types import SimpleNamespace

    return SimpleNamespace(libraries_home=libraries_home,
                           permissions_home=libraries_home / "permissions",
                           rules_home=libraries_home / "rules",
                           routines_home=getattr(server, "routines_home", None),
                           machines=getattr(server, "machines", {}) or {})


def _unmet(surface: dict) -> set[str]:
    return {f"{n['severity']}:{n['id']}" for n in surface["nodes"]
            if n["severity"] in ("blocks", "interrupts")}


def _holds(server: Any, cfg: Any, kind: str, name: str, catalog: list[dict]) -> bool:
    """Does this routine hold the named document?"""
    from .readmodels.surface import _held_utils

    if kind == "rule":
        return name in (cfg.rules or [])
    if kind == "permission":
        return name in (cfg.permissions or [])
    # the calls closure includes the held util itself, so the direct case is covered by it
    return any(name in _calls_closure(server.libraries_home, h)
               for h in _held_utils(cfg, catalog))


def holders(server: Any, kind: str, name: str) -> list[str]:
    """Every routine that would feel a change to this document, newest question first.

    For a util that is "holds it as a reserved util", INCLUDING transitively: a util's `calls:`
    line pulls its callees' secrets and filesystem needs into the same jail, so revising a util
    a routine never names can still change what the util it DOES name requires.
    """
    from .config import load_routine
    from .utils_lib import list_utils

    out: list[str] = []
    catalog = list_utils(server.libraries_home) if kind == "util" else []
    for d in _routine_dirs(server):
        cfg, _ = load_routine(d)
        if cfg is None:
            continue
        if _holds(server, cfg, kind, name, catalog):
            out.append(cfg.slug)
    return out


def _calls_closure(lib: Path, name: str) -> set[str]:
    """Every util reachable from `name` over declared `calls:` lines — one jail, one env."""
    from .utils_header import parse_header
    from .utils_lib import read_util

    seen: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        src = read_util(lib, current)
        if src is not None:
            stack += parse_header(src)["calls"]
    return seen


def impact(server: Any, kind: str, name: str, content: str | None) -> dict:
    """What writing `content` to `<kind>/<name>` would do to the routines that hold it.

    `content=None` means DELETION. Returns `{holders, breaks, unaffected, digest}`; `breaks`
    names each routine that gains a blocking or interrupting row, with the rows it gains.

    `digest` fingerprints the answer. The web save carries it back and the write is refused if
    it no longer matches — so a library that moved between preview and save re-prompts instead
    of letting somebody approve an impact they were never shown.
    """
    from .config import load_routine
    from .readmodels.surface import routine_surface

    who = holders(server, kind, name)
    breaks: list[dict] = []
    unaffected: list[str] = []
    if who:
        by_slug = {d.name: d for d in _routine_dirs(server)}
        with _shadow_library(Path(server.libraries_home), kind, name, content) as shadow:
            after_server = _server_at(server, shadow)
            for slug in who:
                d = by_slug.get(slug)
                if d is None:
                    continue
                cfg, _ = load_routine(d)
                if cfg is None:
                    continue
                try:
                    before = _unmet(routine_surface(server, cfg))
                    after = _unmet(routine_surface(after_server, cfg))
                except (OSError, ValueError):
                    continue          # a diagnostic must never be the reason a write fails
                gained = sorted(after - before)
                if gained:
                    breaks.append({"slug": slug, "gains": gained})
                else:
                    unaffected.append(slug)
    payload = f"{kind}:{name}:{sorted(who)}:{[b['slug'] for b in breaks]}"
    return {"holders": who, "breaks": breaks, "unaffected": unaffected,
            "digest": hashlib.sha256(payload.encode()).hexdigest()[:16]}


def impact_lines(result: dict) -> list[str]:
    """The impact as flat text — what the approval question appends and the CLI prints."""
    if not result["holders"]:
        return ["binds no routine yet"]
    out = [f"binds: {', '.join(result['holders'])}"]
    for b in result["breaks"]:
        for gained in b["gains"]:
            severity, _, eid = gained.partition(":")
            out.append(f"BREAKS {b['slug']}: {eid} ({severity})")
    if not result["breaks"]:
        out.append("breaks none of them")
    return out
