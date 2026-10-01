"""The SETUP SURFACE — one join answering "what does this routine still need?".

The setup layers (rules, conduct docs, capabilities, secrets, filesystem roots, machines,
connections) have real interdependencies, but until now the system declared exactly ONE of
them: a permission doc's `requires:`, pointing at a capability. Everything else was true but
unwritten, so nothing could render it, lint it or warn about it — a routine holding
`remote-machines` with no bound machine looked identical to one that was ready; the gap
surfaced only when a run burned a turn on an empty host list.

This module is the forward reading of the dependency graph. It joins, per routine:

- the routine's config: held permissions, bound rules, the capability mapping, grants, fs
  roots, machines, connections;
- the library's declarations: a permission's `requires:` (necessary, enforced by the cascade)
  and `expects:` (optional, presumed — legal on rules too, where `requires:` is a lint error);
- the UTIL HEADERS of every reserved util the routine holds, walked transitively over `calls:`
  — their `secrets:` and their `fs:` private stores are dependency edges nobody had joined to
  the routine that holds them;
- the SCHEDULE, against the lane store: a member cron a lane's schedule silently suppresses,
  plus a routine nothing on a clock ever starts;
- the live stores: the secrets store, the machine catalog, the connection registry.

Nothing is stored. The library MOVES — routines author and revise the utils and rules it is
made of, one copy each, reaching every holder at its next run — so a resolution persisted into
routine.yaml would be stale the first time somebody ran `write_util`. It is recomputed at every
read, which is what lets the same function answer for the routine page, for `rsched validate`
and at run boot.

The reverse reading ("who depends on this thing?") is a separate question with a separate
consumer — the authoring approval — and lives apart. So does the PROSE reading: rendering
these nodes as lines for an operator with no panel to click is `remedies.py`, a different
audience rather than a different join.

The JOIN is here; the four things it joins are four modules, because a file that grew a
`fix` kind every week reached 663 lines and every change re-read all of them to find the one
it touched. `surface_nodes` is the row vocabulary (severities, `_node`, the merge rule),
`surface_needs` the declared needs (util secrets, util stores, `expects:`),
`surface_schedule` the "what starts this routine" checks, `surface_caps` the capability
coverage and the act that settles each gap. Each emits rows; this file decides what to ask
and counts the verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .surface_caps import _absent_util_node, _uncovered_nodes
from .surface_needs import _expects_nodes, _fs_nodes, _secret_nodes
from .surface_nodes import _ORDER, BLOCKS, INTERRUPTS, NOTE, _node, _one_row_per_entity
from .surface_schedule import _phase_nodes, _schedule_nodes

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig


def _held_utils(cfg: RoutineConfig) -> list[str]:
    """Every reserved util this routine may call — the names (and `name:verb` entries) it
    holds.
    """
    return sorted(set((cfg.capabilities or {}).get("utils") or []))


def _util_held(held: set[str], entry: str) -> bool:
    """Does a routine holding `held` honour a doc reserving `entry`? A bare grant covers
    every verb; a verb grant under a doc reserving the whole util is a deliberate narrowing
    (read-only access to a channel), which is still a grant and not a shortfall.
    """
    from ..grants import split_util_verb

    bare, verb = split_util_verb(entry)
    return (entry in held or bare in held
            or (not verb and any(split_util_verb(h)[0] == bare for h in held)))


def routine_surface(server: Any, cfg: RoutineConfig) -> dict:
    """The full setup surface for one routine: `{nodes, verdict}`.

    `nodes` are typed, each carrying WHY it is needed and what an unmet need costs. `verdict`
    counts them so a caller can decide at a glance whether to shout.
    """
    from .. import utils_run
    from ..secrets import load_secrets
    from . import library_reads

    lib_home = server.libraries_home
    catalog = library_reads.utils(lib_home)
    by_name = {u["name"]: u for u in catalog}

    nodes: list[dict] = []

    # -- the soft edge, from held docs AND bound rules (a rule may expect, never require) ----
    expects = {}
    perm_expects = library_reads.expects(server.permissions_home)
    rule_expects = library_reads.expects(server.rules_home)
    for slug in cfg.permissions or []:
        if slug in perm_expects:
            expects[slug] = perm_expects[slug]
    for slug in cfg.rules or []:
        if slug in rule_expects:
            expects[slug] = rule_expects[slug]
    machine_catalog = getattr(server, "machines", {}) or {}
    nodes += _expects_nodes(cfg, expects, machine_catalog)
    nodes += _schedule_nodes(server, cfg)
    nodes += _phase_nodes(server, cfg)

    # The library's `requires:`, read once: the util join below asks it (a covering doc decides
    # an absent util's remedy), as do the checks after it.
    lib_requires = library_reads.requires(server.permissions_home)

    # -- the util-header join: what the RESERVED utils this routine holds actually need ------
    # Utils already declare their secrets and their private filesystem stores; this is the
    # first thing that reads those declarations on behalf of the routine holding the util.
    secret_needs: dict[str, list[str]] = {}
    fs_needs: list[tuple[str, str, str, str]] = []
    from ..grants import split_util_verb

    for name in sorted({split_util_verb(u)[0] for u in _held_utils(cfg)}):
        if name not in by_name:
            nodes.append(_absent_util_node(cfg, lib_requires, name))
            continue
        needs = utils_run.util_needs(lib_home, name)
        for secret in sorted(needs.secrets - needs.optional):
            secret_needs.setdefault(secret, []).append(name)
        for mode, path in needs.fs_paths:
            fs_needs.append((mode, path,
                             f"the {name} util declares it as a private store", name))
    nodes += _secret_nodes(cfg, secret_needs, set(load_secrets()))
    nodes += _fs_nodes(cfg, fs_needs)

    # -- held docs whose requirements the mapping does not cover. The save-time floor makes
    #    this impossible through the UI, so a hit means the file was edited by hand. --------
    caps = cfg.capabilities or {}
    # -- a routine that may rewrite its OWN instructions. Never wrong — it is the whole job of
    #    an improver — but it is the one capability whose effect is the routine itself, so it
    #    is always said out loud. (Before 0.261.0 this was a side effect of an fs write root
    #    covering the routine dir; it is a switch now, which is why the note names the switch.)
    #    It carries no `fix`: nothing here is unmet. Offering to undo a deliberate switch
    #    would read as a defect report on a routine set up exactly as intended. The uncovered
    #    check below reaches this same id WITH one; `_one_row_per_entity` states which survives,
    #    so this append may sit anywhere in the function.
    if "write_recipe" in (caps.get("actions") or []):
        nodes.append(_node("action:write_recipe", "on", NOTE,
                           "this routine may rewrite its own instructions",
                           "main.md / stages/ / tuning.yaml are writable by its runs; "
                           "routine.yaml stays sealed", source={"doc": "recipe-authoring"}))

    held_utils = set(_held_utils(cfg))
    for slug in cfg.permissions or []:
        req = lib_requires.get(slug) or {}
        missing = [a for a in req.get("actions") or [] if a not in (caps.get("actions") or [])]
        missing += [f"util:{u}" for u in req.get("utils") or [] if not _util_held(held_utils, u)]
        if missing:
            # The same list twice, once for each audience: `effect` says what it costs, `fix`
            # hands the exact switches over so the offer can read "switch on shell"
            # rather than "go and look".
            nodes.append(_node(f"permission:{slug}", "unsatisfied", BLOCKS,
                               "held, but its requires: are not switched on",
                               "enforcement reads capabilities only, so it fails closed: "
                               + ", ".join(missing),
                               {"kind": "switch_on", "entity": slug, "missing": missing},
                               {"doc": slug}))

    nodes += _uncovered_nodes(cfg, lib_requires)
    nodes = _one_row_per_entity(nodes)

    counts = {BLOCKS: 0, INTERRUPTS: 0, NOTE: 0}
    for n in nodes:
        if n["severity"] in counts:
            counts[n["severity"]] += 1
    nodes.sort(key=lambda n: (_ORDER[n["severity"]], n["id"]))
    return {
        "nodes": nodes,
        "verdict": {"ready": counts[BLOCKS] == 0 and counts[INTERRUPTS] == 0,
                    "blocks": counts[BLOCKS], "interrupts": counts[INTERRUPTS],
                    "notes": counts[NOTE]},
    }


#: What the BOOT note carries. The note's own closing sentence explains FAIL and WARN and
#: nothing else, because it exists for gaps the RUN would otherwise discover the hard way. A
#: NOTE is addressed to the operator — a cron the lane suppresses, a phase file keyed wrong —
#: and the run can neither act on it nor be saved a turn by it, so putting one in front of
#: every run buys prompt noise. `rsched validate` and the routine page still show all three.
#:
#: The prose rendering of these nodes — every `fix` kind said in words — is `remedies.py`.
BOOT_SEVERITIES = (BLOCKS, INTERRUPTS)
