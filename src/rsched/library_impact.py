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
reports whoever gains a blocking or interrupting row — plus, since D222, whoever has DECLINED
the entity the change newly needs, whose line says what the decline costs instead of calling it
broken. So the approval question and the routine page can never disagree about what a gap means:
it is one function, read forwards, twice.
"""

from __future__ import annotations

import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .readmodels.surface_nodes import BLOCKS, INTERRUPTS, NOTE

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
    copy: ServerConfig is a validated model and the surface only ever reads these five.
    """
    from types import SimpleNamespace

    return SimpleNamespace(libraries_home=libraries_home,
                           permissions_home=libraries_home / "permissions",
                           rules_home=libraries_home / "rules",
                           routines_home=getattr(server, "routines_home", None),
                           machines=getattr(server, "machines", {}) or {})


#: The severities a GAP carries, plus the one settled state that is not a gap and still changes
#: what a holder gets. D222 (operator, 2026-10-30): F609 rightly stopped a recorded decline
#: reading as a gap — the surface no longer offers to revert his own decision — but `_unmet`
#: collects exactly the gap severities, so a routine that DECLINED a secret a util newly declares
#: stopped appearing in the authoring question's break list, while genuinely not getting that
#: credential. His answer was (b): *"Widen library_impact._unmet to include the `denied` note, so
#: the routine appears — worded as 'declined, so this util will not work there' rather than as
#: broken, which informs you without proposing to revert your decline"*. The distinction lives in
#: the WORDING (`impact_lines`), not in a second rule about when a decline counts: this module's
#: promise is one function read forwards twice, and a second rule here would recreate exactly the
#: divergence between the question and the routine page that it exists to prevent.
_GAP_SEVERITIES = (BLOCKS, INTERRUPTS)
_DECLINED_STATE = "denied"


def _unmet(surface: dict) -> set[str]:
    return {f"{n['severity']}:{n['id']}" for n in surface["nodes"]
            if n["severity"] in _GAP_SEVERITIES or n.get("state") == _DECLINED_STATE}


def _holds(server: Any, cfg: Any, kind: str, name: str) -> bool:
    """Does this routine hold the named document?"""
    from .grants import split_util_verb
    from .readmodels.surface import _held_utils

    if kind == "rule":
        return name in (cfg.rules or [])
    if kind == "permission":
        return name in (cfg.permissions or [])
    # the calls closure includes the held util itself, so the direct case is covered by it
    return any(name in _calls_closure(server.libraries_home, h)
               for h in {split_util_verb(e)[0] for e in _held_utils(cfg)})


def holders(server: Any, kind: str, name: str) -> list[str]:
    """Every routine that would feel a change to this document, newest question first.

    For a util that is "holds it as a reserved util", INCLUDING transitively: a util's `calls:`
    line pulls its callees' secrets and filesystem needs into the same jail, so revising a util
    a routine never names can still change what the util it DOES name requires.
    """
    from .config import load_routine

    out: list[str] = []
    for d in _routine_dirs(server):
        cfg, _ = load_routine(d)
        if cfg is None:
            continue
        if _holds(server, cfg, kind, name):
            out.append(cfg.slug)
    return out


def _reserved_by(server: Any, name: str) -> tuple[list[str], list[str]]:
    """Which library permission docs reserve this util — `(whole, verbs)`.

    The same join `policyload.load_policy` builds its `gated_utils`/`gated_verbs` from, read
    for ONE util: a BARE `name` in a doc's `requires.utils` reserves the whole util, while
    `name:verb` reserves that one verb and leaves the rest open. A verb reservation is dropped
    once some doc reserves the util whole, exactly as the policy drops it — otherwise two
    readers of one library could disagree about what is gated.
    """
    from .grants import read_library_requires, split_util_verb

    whole: set[str] = set()
    verbs: set[str] = set()
    for slug, req in read_library_requires(Path(server.permissions_home)).items():
        for entry in req.get("utils") or []:
            bare, verb = split_util_verb(entry)
            if bare != name:
                continue
            if verb:
                verbs.add(verb)
            else:
                whole.add(slug)
    return sorted(whole), ([] if whole else sorted(verbs))


def reach(server: Any, kind: str, name: str) -> dict | None:
    """WHO MAY CALL this util, which is not who HOLDS it — `None` for a rule or a permission.

    The operator, 2026-11-18, on `code-search`'s page: *"noone holds this util?! that's not
    true"*. He was right, and the emptiness was real: `holders` answers "who holds a RESERVED
    grant", and the library reserves a util only where some permission doc's `requires.utils`
    names it. Seven of today's 56 utils are reserved; every other one — `code-search`, `rsched`,
    `runs`, `json`, `git` — is callable by every routine, and the engine rewrites the
    `code_search` and `websearch` ACTION KINDS (D290, `engine/kindaliases.py`) into a util call
    before any gate sees them. So an empty holder list was being printed as "a change here
    reaches nobody" for the utils the fleet depends on MOST, and the same computation gates
    deletion.

    This is deliberately a SECOND fact beside `holders` rather than a wider holder list. A
    holder is a routine the break analysis can reason about: `readmodels/surface.py` joins util
    headers through `_held_utils`, the RESERVED grants, so an open util's secrets and roots
    enter no routine's surface and listing all of them would pair a 29-name list with "breaks
    none of them" — a second falsehood, bought at ~1.6 s of surface computation per preview on
    a UI the operator already calls slow (measured 2026-11-18: 29 surfaces in 1.60 s, and an
    impact does them twice).
    """
    if kind != "util":
        return None
    whole, verbs = _reserved_by(server, name)
    return {"open_to_all": not whole, "gated_by": whole, "gated_verbs": verbs,
            "fleet": len(_routine_dirs(server))}


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

    `digest` fingerprints the answer — who holds the document and what each broken holder gains,
    not only which holders break. The web save carries it back and the write is refused if it
    no longer matches — so a library that moved between preview and save re-prompts instead of
    letting somebody approve an impact they were never shown.
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
    payload = f"{kind}:{name}:{sorted(who)}:{[(b['slug'], b['gains']) for b in breaks]}"
    return {"holders": who, "breaks": breaks, "unaffected": unaffected,
            "reach": reach(server, kind, name),
            "digest": hashlib.sha256(payload.encode()).hexdigest()[:16]}


def impact_lines(result: dict) -> list[str]:
    """The impact as flat text — what the approval question appends and the CLI prints."""
    reached = result.get("reach") or {}
    if not result["holders"]:
        if reached.get("open_to_all") and reached.get("fleet"):
            # NEVER "binds no routine yet" for an open util: no routine holds a grant for it
            # because none is needed, and every one of them may call it (the operator, 2026-11-18).
            # An EMPTY fleet is the one case where the old wording stays true — nothing can call
            # a util when no routine is installed, and claiming "every routine (0)" would be the
            # same overclaim in the other direction.
            every = f"every routine ({reached['fleet']}) may call it"
            return [f"reserved by no permission doc — {every}"]
        return ["binds no routine yet"]
    out = [f"binds: {', '.join(result['holders'])}"]
    if reached.get("open_to_all") and reached.get("gated_verbs"):
        out.append(f"only {', '.join(reached['gated_verbs'])} is reserved — every routine "
                   f"({reached['fleet']}) may call its other verbs")
    broke = False
    for b in result["breaks"]:
        for gained in b["gains"]:
            severity, _, eid = gained.partition(":")
            if severity == NOTE:
                # D222: a holder that DECLINED this entity appears, and its line says what the
                # decline costs rather than calling it broken — the operator decided this, and a
                # break list that proposed reverting his own decision is the F609 defect.
                out.append(f"{b['slug']}: {eid} declined, so this util will not work there")
            else:
                broke = True
                out.append(f"BREAKS {b['slug']}: {eid} ({severity})")
    if not broke:
        out.append("breaks none of them")
    return out
