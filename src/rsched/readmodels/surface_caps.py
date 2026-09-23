"""The setup surface's CAPABILITY-COVERAGE join — which capabilities this routine switched
on that no conduct doc it holds covers, and WHERE each one could be dropped.

Enforcement reads capabilities only, deliberately, so the doc layer can never widen what a
run may do — the cost being that a mapping CAN carry a gated kind or a reserved util with no
conduct behind it, however it got there (a hand-edited file, a restored backup, a domain's
shared block). That is reported here, per routine.

The `owner` half is why the drop site is computed and not assumed: a routine's own save
FLOORS its mapping, but a domain's shared block is not floored and a member's list UNIONS
with it at every load — so an entry the domain supplies comes straight back the moment the
member drops it, and only the domain's editor can remove that one. Split out of `surface.py`
at 663 lines; the row vocabulary is `surface_nodes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .surface_nodes import BLOCKS, NOTE, OWNER_DOMAIN, OWNER_ROUTINE, _node

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig

def _domain_capabilities(server: Any, cfg: RoutineConfig) -> tuple[dict, str, str]:
    """The DOMAIN's own shared capability block, with the domain's id and its name.

    Read from the store rather than from `cfg.inherited`, which records what the merge
    CONTRIBUTED and answers a different question — wrongly in both directions. The lists
    UNION, so an entry the member's file also names contributes nothing while still surviving
    every drop the member makes; and an entry the member alone set reads as inherited whenever
    the domain happened to supply some other one. One fact settles where a capability can be
    dropped — whether the domain's block names it — so that is the fact read here.
    """
    from .. import domains

    domain_id = getattr(cfg, "domain", "") or ""
    rec = domains.get(server.routines_home, domain_id) if domain_id else None
    if not rec:
        return {}, "", ""       # a member naming a deleted domain inherits nothing
    shared = (rec.get("config") or {}).get("capabilities") or {}
    return (shared if isinstance(shared, dict) else {}), domain_id, str(rec.get("name") or "")


def _drop_site(entry: str, shared: object, domain_id: str, name: str) -> tuple[str, dict, dict]:
    """Where dropping `entry` actually works, said three ways: the suffix the row's prose
    carries, the routing half of its `fix`, plus the `source` that names the domain by id.

    The fix says `owner` — `OWNER_ROUTINE` or `OWNER_DOMAIN` — positively for both cases, so a
    reader of the payload is never inferring the routine's own page from an absent key, with
    the domain's NAME beside it because both renderings put it in a sentence. The ID is
    PROVENANCE rather than remedy ("which thing put this row here"), so it rides `source`,
    where a link that wants to address one domain rather than the list will find it.
    """
    if entry not in (shared if isinstance(shared, list) else []):
        return "", {"owner": OWNER_ROUTINE}, {}
    return (f" (inherited from the domain {name!r})",
            {"owner": OWNER_DOMAIN, "domain": name}, {"domain": domain_id})


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


def _absent_util_node(cfg: RoutineConfig, lib_requires: dict, name: str,
                      domain: tuple[dict, str, str]) -> dict:
    """A reserved util the library does not have — plus the ONE act that settles it.

    Nobody writes a util by hand, only a run does through `write_util`, so the performable half
    is always to stop holding it — and WHERE that works is three different places. A covering
    doc is asked FIRST, because while one is held no drop survives the next save
    (`_covering_doc`): the fix carries that slug and nothing else, so neither reader can offer
    an act that undoes itself. Otherwise `_drop_site` answers it exactly as it does for an
    uncovered capability — the routine's own mapping, or the DOMAIN's shared block the member's
    list unions with at every load.
    """
    effect = "no util by that name is in the library"
    doc = _covering_doc(cfg, lib_requires, name)
    if doc:
        return _node(f"util:{name}", "absent", BLOCKS,
                     f"held as a reserved util, required by the conduct doc {doc!r}", effect,
                     {"kind": "install_util", "name": name, "doc": doc}, {"doc": doc})
    shared, domain_id, domain_name = domain
    where, site, src = _drop_site(name, shared.get("utils"), domain_id, domain_name)
    return _node(f"util:{name}", "absent", BLOCKS, "held as a reserved util" + where, effect,
                 {"kind": "install_util", "name": name, **site}, src)


def _uncovered_nodes(cfg: RoutineConfig, lib_requires: dict,
                     domain: tuple[dict, str, str]) -> list[dict]:
    """The INVERSE misconfiguration: a capability no held doc asks for.

    Three deliberate designs meet here and none of them catches it on its own. The floor is a
    WRITE-time invariant on a routine's OWN mapping. A DOMAIN's config block is deliberately
    not floored at its own save, because a member may hold the covering doc itself. And
    enforcement is deliberately capabilities-ONLY, so that prose can never widen anything —
    which also means an orphan capability is simply obeyed. So a domain can hand its members a
    reserved util with no conduct doc behind it, with nothing anywhere saying a word.

    Nothing is broken when it happens: the routine really can do the thing. What is wrong is
    that it can do it for a reason the permissions panel does not show, so it is reported.

    Each row names WHERE its drop is performed (`_drop_site`). Provenance is most of the value
    of these rows in prose — "you did not set this, your DOMAIN did" — and all of it in the
    payload: the routine page can drop what the routine owns and nothing else.
    """
    from ..grants import _DEFAULT_KIND_SOURCE, split_util_verb

    caps = cfg.capabilities or {}
    covering = {u for slug in cfg.permissions or []
                for u in (lib_requires.get(slug) or {}).get("utils") or []}
    covering_actions = {a for slug in cfg.permissions or []
                        for a in (lib_requires.get(slug) or {}).get("actions") or []}
    held_docs = set(cfg.permissions or [])
    covering_names = {split_util_verb(u)[0] for u in covering}
    shared, domain_id, domain_name = domain
    out: list[dict] = []
    for util in caps.get("utils") or []:
        if util in covering or split_util_verb(util)[0] in covering_names:
            continue
        where, site, src = _drop_site(util, shared.get("utils"), domain_id, domain_name)
        out.append(_node(f"util:{util}", "uncovered", NOTE,
                         "switched on, but no held conduct doc requires it" + where,
                         "the run may call it, with none of the conduct prose that "
                         "normally comes with it",
                         {"kind": "cover_or_drop", "entity": f"util:{util}", **site}, src))
    for action in caps.get("actions") or []:
        if action in covering_actions or _DEFAULT_KIND_SOURCE.get(action) in held_docs:
            continue
        where, site, src = _drop_site(action, shared.get("actions"), domain_id, domain_name)
        out.append(_node(f"action:{action}", "uncovered", NOTE,
                         "switched on, but no held conduct doc requires it" + where,
                         "the run may use it, with none of the conduct prose that "
                         "normally comes with it",
                         {"kind": "cover_or_drop", "entity": f"action:{action}", **site}, src))
    return out
