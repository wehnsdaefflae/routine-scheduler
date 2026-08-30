"""The SETUP SURFACE — one join answering "what does this routine still need?".

The setup layers (rules, conduct docs, capabilities, secrets, filesystem roots, machines,
connections) have real interdependencies, but until now the system declared exactly ONE of
them: a permission doc's `requires:`, pointing at a capability. Everything else was true but
unwritten, so nothing could render it, lint it or warn about it — a routine holding
`remote-machines` with no bound machine looked identical to one that was ready, and the gap
surfaced only when a run burned a turn on an empty host list.

This module is the forward reading of the dependency graph. It joins, per routine:

- the EFFECTIVE config (group inheritance already merged by the registry): held permissions,
  bound rules, the capability mapping, grants, fs roots, machines, connections;
- the library's declarations: a permission's `requires:` (necessary, enforced by the cascade)
  and `expects:` (optional, presumed — legal on rules too, where `requires:` is a lint error);
- the UTIL HEADERS of every reserved util the routine holds, walked transitively over `calls:`
  — their `secrets:` and their `fs:` private stores are dependency edges nobody had joined to
  the routine that holds them;
- the live stores: the secrets store, the machine catalog, the connection registry.

Nothing is stored. The library MOVES — routines author and revise the utils and rules it is
made of, one copy each, reaching every holder at its next run — so a resolution persisted into
routine.yaml would be stale the first time somebody ran `write_util`. It is recomputed at every
read, which is what lets the same function answer for the routine page, for `rsched validate`
and at run boot.

The reverse reading ("who depends on this thing?") is a separate question with a separate
consumer — the authoring approval — and lives apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig

# What an unmet need COSTS, worst first. The vocabulary is deliberately about consequence, not
# about severity in the abstract: the operator's question is "will this break, or interrupt, or
# is it just worth knowing?", and every row has to answer it.
BLOCKS = "blocks"           # the call is rejected or fails — the run cannot do the thing
INTERRUPTS = "interrupts"   # the run stops mid-way to ask, spending a turn and your attention
NOTE = "note"               # worth knowing; nothing is broken
OK = "ok"
_ORDER = {BLOCKS: 0, INTERRUPTS: 1, NOTE: 2, OK: 3}


def _covered(path: Path, roots: list[Path]) -> bool:
    """Is `path` inside (or equal to) one of `roots`? The same containment the sandbox uses."""
    return any(path == root or root in path.parents for root in roots)


def _held_utils(cfg: RoutineConfig, catalog: list[dict]) -> list[str]:
    """Every reserved util this routine may call: the names it holds, plus every util carrying
    a gated TAG. Tag gating covers utils the library gains later, so it is read from the live
    catalog rather than from the mapping.
    """
    caps = cfg.capabilities or {}
    names = set(caps.get("utils") or [])
    tags = set(caps.get("util_tags") or [])
    if tags:
        names |= {u["name"] for u in catalog if tags & set(u.get("tags") or [])}
    return sorted(names)


def _node(eid: str, state: str, severity: str, why: str, effect: str = "",
          fix: dict | None = None, source: dict | None = None) -> dict:
    """One typed need. `source` is machine-readable PROVENANCE — which conduct doc or which
    util put this row here — so a UI can group rows under the ability that owns them without
    parsing `why`. Joining on prose is exactly what invariant 5 forbids.
    """
    return {"id": eid, "state": state, "severity": severity, "why": why,
            "effect": effect, "fix": fix or {}, "source": source or {}}


def _secret_nodes(cfg: RoutineConfig, needed: dict[str, list[str]],
                  store_keys: set[str]) -> list[dict]:
    """A declared secret is a dependency of whoever declares it. Four outcomes, and the
    distinction between them is the whole point: absent from the store BLOCKS, undecided
    INTERRUPTS (one blocking access request at the first call), denied forever BLOCKS.

    Two families are NOT store secrets and must not be reported as missing from it: the machine
    vars the engine injects from a binding, and a connection's `<PROVIDER>_ACCESS_TOKEN`. Their
    real dependency is the binding, which the expects: join already covers.
    """
    from ..machines import machine_env_vars

    out = []
    engine_injected = machine_env_vars()
    grants = cfg.grants or {}
    for name in sorted(needed):
        if name in engine_injected or name.endswith("_ACCESS_TOKEN"):
            continue
        eid = f"secret:{name}"
        why = "needed by " + ", ".join(sorted(needed[name]))
        src = {"utils": sorted(needed[name])}
        decided = grants.get(eid)
        if name not in store_keys:
            out.append(_node(eid, "absent", BLOCKS, why,
                             "not in the secrets store — the call runs without it",
                             {"kind": "add_secret", "name": name}, src))
        elif decided is False:
            out.append(_node(eid, "denied", BLOCKS, why,
                             "declined forever — the run no longer asks and the call fails",
                             {"kind": "clear_grant", "entity": eid}, src))
        elif decided is True:
            out.append(_node(eid, "exposed", OK, why, source=src))
        else:
            out.append(_node(eid, "undecided", INTERRUPTS, why,
                             "the first call declaring it stops the run to ask you",
                             {"kind": "grant", "entity": eid}, src))
    return out


def _fs_nodes(cfg: RoutineConfig, needed: list[tuple[str, str, str, str]]) -> list[dict]:
    """`needed` is (mode, path, why). A private store a util declares is only reachable when a
    granted root covers it — the declaration narrows, it never asks — so an uncovered one is a
    hard block, not a prompt.

    `$VAR` resolves exactly as `sandbox.wrap` resolves it (daemon environment, never the run's),
    and an UNSET variable names no path at all, so it is skipped rather than reported: the
    messengers declare both `$X_SESSION_DIR` and its literal default, and only one of the two
    ever resolves.
    """
    import os

    out = []
    write_roots = [Path(p) for p in cfg.fs_write_roots or []]
    read_roots = [Path(p) for p in cfg.fs_read_roots or []]
    seen: set[str] = set()
    for mode, declared, why, util in needed:
        raw = os.path.expandvars(declared)
        if "$" in raw or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw).expanduser()
        eid = f"fs-{'write' if mode == 'rw' else 'read'}:{raw}"
        ok = (_covered(path, write_roots) if mode == "rw"
              else _covered(path, write_roots + read_roots))
        src = {"utils": [util]}
        if ok:
            out.append(_node(eid, "granted", OK, why, source=src))
        else:
            out.append(_node(eid, "missing", BLOCKS, why,
                             "no granted root covers it — the util cannot reach it",
                             {"kind": "add_root", "mode": mode, "path": raw}, src))
    return out


def _expects_nodes(cfg: RoutineConfig, expects: dict[str, dict],
                   machine_catalog: dict) -> list[dict]:
    """The SOFT edge: entities a doc's or a rule's prose presumes. `"*"` means "at least one of
    this class" — the prose explaining which one lives in the doc body, never in the key.
    """
    out: list[dict] = []
    write_roots = [Path(p) for p in cfg.fs_write_roots or []]
    read_roots = [Path(p) for p in cfg.fs_read_roots or []]
    for slug, mapping in sorted(expects.items()):
        for cls, names in sorted(mapping.items()):
            for name in names:
                eid = f"{cls}:{name}"
                why = f"{slug} expects it"
                src = {"doc": slug}
                if cls == "machine":
                    bound = list(cfg.machines or [])
                    if name == "*":
                        ok, detail = bool(bound), "no machine is bound to this routine"
                    else:
                        ok, detail = name in bound, f"machine {name!r} is not bound"
                    if not ok and not machine_catalog:
                        detail += " (and the machine catalog is empty)"
                    out.append(_node(eid, "bound" if ok else "missing", OK if ok else INTERRUPTS,
                                     why, "" if ok else detail + " — every call returns nothing",
                                     {} if ok else {"kind": "bind_machine"}, src))
                elif cls in ("fs-write", "fs-read"):
                    roots = write_roots if cls == "fs-write" else write_roots + read_roots
                    ok = bool(roots) if name == "*" else _covered(Path(name), roots)
                    out.append(_node(eid, "granted" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "the routine has no root the prose can use",
                                     {} if ok else {"kind": "add_root", "mode": "rw",
                                                    "path": "" if name == "*" else name}, src))
                elif cls == "connection":
                    ok = bool(cfg.connections) if name == "*" else name in (cfg.connections or {})
                    out.append(_node(eid, "bound" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "no account is bound for it", source=src))
                # secret: expectations are covered by the util-header join, which is the
                # authority — re-deriving them here would be a second copy that can drift.
    return out


def routine_surface(server: Any, cfg: RoutineConfig) -> dict:
    """The full setup surface for one routine: `{nodes, verdict}`.

    `nodes` are typed, each carrying WHY it is needed and what an unmet need costs. `verdict`
    counts them so a caller can decide at a glance whether to shout.
    """
    from .. import grants as grants_mod
    from .. import utils_lib, utils_run
    from ..secrets import load_secrets

    lib_home = server.libraries_home
    catalog = utils_lib.list_utils(lib_home)
    by_name = {u["name"]: u for u in catalog}

    nodes: list[dict] = []

    # -- the soft edge, from held docs AND bound rules (a rule may expect, never require) ----
    expects = {}
    perm_expects = grants_mod.read_library_expects(server.permissions_home)
    rule_expects = grants_mod.read_library_expects(server.rules_home)
    for slug in cfg.permissions or []:
        if slug in perm_expects:
            expects[slug] = perm_expects[slug]
    for slug in cfg.rules or []:
        if slug in rule_expects:
            expects[slug] = rule_expects[slug]
    machine_catalog = getattr(server, "machines", {}) or {}
    nodes += _expects_nodes(cfg, expects, machine_catalog)

    # -- the util-header join: what the RESERVED utils this routine holds actually need ------
    # Utils already declare their secrets and their private filesystem stores; this is the
    # first thing that reads those declarations on behalf of the routine holding the util.
    secret_needs: dict[str, list[str]] = {}
    fs_needs: list[tuple[str, str, str, str]] = []
    for name in _held_utils(cfg, catalog):
        if name not in by_name:
            nodes.append(_node(f"util:{name}", "absent", BLOCKS,
                               "held as a reserved util",
                               "no util by that name is in the library"))
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
    lib_requires = grants_mod.read_library_requires(server.permissions_home)
    caps = cfg.capabilities or {}
    # -- a routine that may rewrite its OWN instructions. Never wrong — it is the whole job of
    #    an improver — but it is the one capability whose effect is the routine itself, so it
    #    is always said out loud. (Before 0.261.0 this was a side effect of an fs write root
    #    covering the routine dir; it is a switch now, which is why the note names the switch.)
    if "write_recipe" in (caps.get("actions") or []):
        nodes.append(_node("action:write_recipe", "on", NOTE,
                           "this routine may rewrite its own instructions",
                           "main.md / stages/ / tuning.yaml are writable by its runs; "
                           "routine.yaml stays sealed", source={"doc": "recipe-authoring"}))

    covered_utils = set(_held_utils(cfg, catalog))    # names AND everything a gated tag covers
    for slug in cfg.permissions or []:
        req = lib_requires.get(slug) or {}
        missing = [a for a in req.get("actions") or [] if a not in (caps.get("actions") or [])]
        missing += [f"util:{u}" for u in req.get("utils") or [] if u not in covered_utils]
        if missing:
            nodes.append(_node(f"permission:{slug}", "unsatisfied", BLOCKS,
                               "held, but its requires: are not switched on",
                               "enforcement reads capabilities only, so it fails closed: "
                               + ", ".join(missing), source={"doc": slug}))

    # -- the INVERSE misconfiguration: a capability no held doc asks for. -----------------
    # Three deliberate designs meet here and none of them catches it on its own. The floor is
    # a WRITE-time invariant on a routine's OWN mapping. A GROUP's config block is deliberately
    # not floored at its own save, because a member may hold the covering doc itself. And
    # enforcement is deliberately capabilities-ONLY, so that prose can never widen anything —
    # which also means an orphan capability is simply obeyed. So a group can hand its members a
    # reserved util with no conduct doc behind it, and nothing said a word.
    #
    # Nothing is broken when it happens: the routine really can do the thing. What is wrong is
    # that it can do it for a reason the permissions panel does not show, so it is reported.
    covering = {u for slug in cfg.permissions or []
                for u in (lib_requires.get(slug) or {}).get("utils") or []}
    covering_actions = {a for slug in cfg.permissions or []
                        for a in (lib_requires.get(slug) or {}).get("actions") or []}
    from ..grants import _DEFAULT_KIND_SOURCE, split_util_verb
    held_docs = set(cfg.permissions or [])
    covering_names = {split_util_verb(u)[0] for u in covering}
    where = (f" (inherited from the group {cfg.inherited_from!r})"
             if "capabilities" in (getattr(cfg, "inherited", None) or {}) else "")
    for util in caps.get("utils") or []:
        if util not in covering and split_util_verb(util)[0] not in covering_names:
            nodes.append(_node(f"util:{util}", "uncovered", NOTE,
                               "switched on, but no held conduct doc requires it" + where,
                               "the run may call it, with none of the conduct prose that "
                               "normally comes with it"))
    for action in caps.get("actions") or []:
        if action not in covering_actions and _DEFAULT_KIND_SOURCE.get(action) not in held_docs:
            nodes.append(_node(f"action:{action}", "uncovered", NOTE,
                               "switched on, but no held conduct doc requires it" + where,
                               "the run may use it, with none of the conduct prose that "
                               "normally comes with it"))

    # One row per entity. Two checks can legitimately reach the same id — a capability that is
    # both worth naming in its own right and uncovered by any held doc — and two rows saying
    # different things about `action:write_recipe` reads as a bug, not as thoroughness. The
    # worst-severity row wins; the rest are dropped.
    best: dict[str, dict] = {}
    for n in nodes:
        prev = best.get(n["id"])
        if prev is None or _ORDER[n["severity"]] < _ORDER[prev["severity"]]:
            best[n["id"]] = n
    nodes = list(best.values())

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


def surface_lines(surface: dict) -> list[str]:
    """The surface as flat text — what `rsched validate` prints and what the engine files as a
    boot note. One line per unmet node; a ready routine yields nothing at all.
    """
    label = {BLOCKS: "FAIL ", INTERRUPTS: "WARN ", NOTE: "NOTE "}
    out = []
    for n in surface["nodes"]:
        if n["severity"] == OK:
            continue
        tail = f" — {n['effect']}" if n["effect"] else ""
        out.append(f"{label[n['severity']]} {n['id']}: {n['why']}{tail}")
    return out
