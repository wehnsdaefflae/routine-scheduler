"""The setup surface's CAPABILITY-COVERAGE join — which capabilities this routine switched
on that no conduct doc it holds covers, plus the act that settles each one.

Enforcement reads capabilities only, deliberately, so the doc layer can never widen what a
run may do — the cost being that a mapping CAN carry a gated kind or a reserved util with no
conduct behind it, however it got there (a hand-edited file, a restored backup). That is
reported here, per routine. Split out of `surface.py` at 663 lines; the row vocabulary is
`surface_nodes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .surface_nodes import BLOCKS, NOTE, _node

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig


def _covering_doc(cfg: RoutineConfig, lib_requires: dict, util: str) -> str:
    """The held conduct doc whose `requires:` names this util, or `""`.

    Asked of an ABSENT util, where it decides the whole remedy. A save RAISES the mapping to
    cover every held doc before it floors it, so dropping a util a held doc still requires is
    undone by the same save that performs it — the doc is the thing to stop holding. Sorted, so
    a util two docs require names the same one on every read.
    """
    from ..grants import split_util_verb

    for slug in sorted(cfg.permissions or []):
        named = (lib_requires.get(slug) or {}).get("utils") or []
        if util in {split_util_verb(u)[0] for u in named}:
            return slug
    return ""


def _absent_util_node(cfg: RoutineConfig, lib_requires: dict, name: str) -> dict:
    """A reserved util the library does not have — plus the ONE act that settles it.

    Nobody writes a util by hand, only a run does through `write_util`, so the performable half
    is always to stop holding it — and WHERE that works depends on what holds it. A covering
    doc is asked FIRST, because while one is held no drop survives the next save
    (`_covering_doc`): the fix carries that slug and nothing else, so neither reader can offer
    an act that undoes itself. Otherwise the name comes off the routine's own mapping.
    """
    effect = "no util by that name is in the library"
    doc = _covering_doc(cfg, lib_requires, name)
    if doc:
        return _node(f"util:{name}", "absent", BLOCKS,
                     f"held as a reserved util, required by the conduct doc {doc!r}", effect,
                     {"kind": "install_util", "name": name, "doc": doc}, {"doc": doc})
    return _node(f"util:{name}", "absent", BLOCKS, "held as a reserved util", effect,
                 {"kind": "install_util", "name": name})


def _uncovered_nodes(cfg: RoutineConfig, lib_requires: dict) -> list[dict]:
    """The INVERSE misconfiguration: a capability no held doc asks for.

    Two deliberate designs meet here and neither catches it on its own. The floor is a
    WRITE-time invariant, applied by the web layer's saves; a file that reached disk another way
    was never floored. And enforcement is deliberately capabilities-ONLY, so that prose can never
    widen anything — which also means an orphan capability is simply obeyed.

    Nothing is broken when it happens: the routine really can do the thing. What is wrong is
    that it can do it for a reason the permissions panel does not show, so it is reported, with
    its two ways out: hold a doc that requires it, or drop it from the routine's mapping.
    """
    from ..grants import _DEFAULT_KIND_SOURCE, split_util_verb

    caps = cfg.capabilities or {}
    covering = {u for slug in cfg.permissions or []
                for u in (lib_requires.get(slug) or {}).get("utils") or []}
    covering_actions = {a for slug in cfg.permissions or []
                        for a in (lib_requires.get(slug) or {}).get("actions") or []}
    held_docs = set(cfg.permissions or [])
    covering_names = {split_util_verb(u)[0] for u in covering}
    out: list[dict] = []
    for util in caps.get("utils") or []:
        if util in covering or split_util_verb(util)[0] in covering_names:
            continue
        out.append(_node(f"util:{util}", "uncovered", NOTE,
                         "switched on, but no held conduct doc requires it",
                         "the run may call it, with none of the conduct prose that "
                         "normally comes with it",
                         {"kind": "cover_or_drop", "entity": f"util:{util}"}))
    for action in caps.get("actions") or []:
        if action in covering_actions or _DEFAULT_KIND_SOURCE.get(action) in held_docs:
            continue
        out.append(_node(f"action:{action}", "uncovered", NOTE,
                         "switched on, but no held conduct doc requires it",
                         "the run may use it, with none of the conduct prose that "
                         "normally comes with it",
                         {"kind": "cover_or_drop", "entity": f"action:{action}"}))
    return out
