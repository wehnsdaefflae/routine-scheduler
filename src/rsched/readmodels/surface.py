"""The SETUP SURFACE — one join answering "what does this routine still need?".

The setup layers (rules, conduct docs, capabilities, secrets, filesystem roots, machines,
connections) have real interdependencies, but until now the system declared exactly ONE of
them: a permission doc's `requires:`, pointing at a capability. Everything else was true but
unwritten, so nothing could render it, lint it or warn about it — a routine holding
`remote-machines` with no bound machine looked identical to one that was ready; the gap
surfaced only when a run burned a turn on an empty host list.

This module is the forward reading of the dependency graph. It joins, per routine:

- the EFFECTIVE config (domain inheritance already merged by the registry): held permissions,
  bound rules, the capability mapping, grants, fs roots, machines, connections;
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
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig

# What an unmet need COSTS, worst first. The vocabulary is deliberately about consequence, not
# about severity in the abstract: the operator's question is "will this break, or interrupt, or
# is it just worth knowing?" — every row has to answer that.
BLOCKS = "blocks"           # the call is rejected or fails — the run cannot do the thing
INTERRUPTS = "interrupts"   # the run stops mid-way to ask, spending a turn and your attention
NOTE = "note"               # worth knowing; nothing is broken
OK = "ok"
_ORDER = {BLOCKS: 0, INTERRUPTS: 1, NOTE: 2, OK: 3}

# WHERE a drop is performed, which is not one place for every row. A routine's own save FLOORS
# its mapping, so a capability it holds itself is dropped on its own page. A domain's shared
# block is deliberately not floored — a member may hold the covering doc — and a member's list
# UNIONS with the domain's at every load, so a capability the domain supplies is restored the
# moment the member drops it: the only surface that can drop that one is the domain's editor.
# The `fix` carries which, because an offer that travels to a control unable to perform it
# spends the reader's trust as well as their time.
OWNER_ROUTINE = "routine"
OWNER_DOMAIN = "domain"


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

    `fix` is the same idea pointed FORWARDS: a machine-readable REMEDY, so a caller can offer
    the action without parsing `effect`. Its vocabulary is a `kind` plus the parameters that
    kind needs — `{"kind": "add_secret", "name": "FOO_TOKEN"}` — and it names WHAT has to
    happen, never where a UI puts it: `rsched validate` renders these same nodes on a terminal
    with no sections to scroll to and turns each kind into words through `remedies.py`.

    A kind is REGISTERED in five places, of which two are held by a gate:

    - here, the only one that emits it;
    - `readmodels/remedies.py` — the `REMEDIES` table, the same kind in words for the two
      callers with no panel. GATED: `tests/test_surface.py` reads the kinds off this module's
      source and the table's keys together, so a kind with no words fails there;
    - `static/components/surface-view.js` — the `FIX` map, one kind to one panel. `section` is
      the `sec-<id>` anchor every config section heading carries (`components/settings-section.js`
      builds it); `focus` is a selector for the ONE control inside that panel: the ability
      card `abilities.js` stamps `data-ability="<slug>"`, or the orphan row it stamps
      `data-drop="<class>:<name>"`. A kind the map does not name renders no offer at all, which
      is why the console's vocabulary may not lag behind this one;
    - `static/components/setupcheck.js` — the strip above the hero, which renders through
      surface-view's `fixLine` rather than a second map;
    - `tests/ui/test_surface_fix.py` — the `CASES` table, the only registration that asks
      whether the panel a kind lands on can PERFORM the act. GATED:
      `test_no_fix_kind_reaches_the_console_without_a_case_here` holds the console map, the
      CLI wording and this table to one vocabulary.

    UNMET decides who carries one, which is not the same question as severity. Every unmet
    row carries a fix whatever it costs — a cron the file records and the lane overrides is as
    fixable as an absent secret, so both are worth an offer. Every row that is NOT unmet
    carries none. A met row would invite a click to check what is already true; a row reporting
    a deliberate switch (`action:write_recipe` "on") or a finished routine (`schedule:goal`
    "retired") would offer to UNDO it, which reads as a defect report on a routine that is
    exactly right. Two NOTE rows sitting in one table, one offering an action and one not, is
    how a reader learns that an absent fix means nothing in particular — so it means this.
    Where both readings land on ONE entity, `_one_row_per_entity` keeps the fix-less one.
    """
    return {"id": eid, "state": state, "severity": severity, "why": why,
            "effect": effect, "fix": fix or {}, "source": source or {}}


def _secret_nodes(cfg: RoutineConfig, needed: dict[str, list[str]],
                  store_keys: set[str]) -> list[dict]:
    """A declared secret is a dependency of whoever declares it. Four outcomes, whose
    distinction is the whole point: absent from the store BLOCKS, undecided INTERRUPTS (one
    blocking access request at the first call), denied forever BLOCKS.

    Two families are NOT store secrets and must not be reported as missing from it: the machine
    vars the engine injects from a binding, plus a connection's `<PROVIDER>_ACCESS_TOKEN`. Their
    real dependency is the binding, which the expects: join already covers.
    """
    from ..machines import machine_env_vars

    out = []
    # Names the ENGINE supplies per run, not the store: reporting them as missing would send
    # the operator looking for a secret to add that nothing can add.
    engine_injected = machine_env_vars() | {"RSCHED_ROUTINE", "RSCHED_API_TOKEN"}
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

    `$VAR` resolves exactly as `sandbox.wrap` resolves it (daemon environment, never the run's);
    an UNSET variable names no path at all, so it is skipped rather than reported: the
    messengers declare both `$X_SESSION_DIR` and its literal default; only one of the two
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
        # The declaration's own vocabulary is rw/ro; a root is granted as read or write. The
        # fix speaks the granting side, because that is the thing the operator has to do.
        axis = "write" if mode == "rw" else "read"
        eid = f"fs-{axis}:{raw}"
        ok = (_covered(path, write_roots) if mode == "rw"
              else _covered(path, write_roots + read_roots))
        src = {"utils": [util]}
        if ok:
            out.append(_node(eid, "granted", OK, why, source=src))
        else:
            out.append(_node(eid, "missing", BLOCKS, why,
                             "no granted root covers it — the util cannot reach it",
                             {"kind": "add_root", "mode": axis, "path": raw}, src))
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
                                     {} if ok else {"kind": "bind_machine", "name": name}, src))
                elif cls in ("fs-write", "fs-read"):
                    roots = write_roots if cls == "fs-write" else write_roots + read_roots
                    ok = bool(roots) if name == "*" else _covered(Path(name), roots)
                    # `"*"` asks for a root, not for THAT root, so the fix names no path and
                    # the panel offers the empty field rather than a path nobody wrote down.
                    axis = "write" if cls == "fs-write" else "read"
                    out.append(_node(eid, "granted" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "the routine has no root the prose can use",
                                     {} if ok else {"kind": "add_root", "mode": axis,
                                                    "path": "" if name == "*" else name}, src))
                elif cls == "connection":
                    ok = bool(cfg.connections) if name == "*" else name in (cfg.connections or {})
                    out.append(_node(eid, "bound" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "no account is bound for it",
                                     {} if ok else {"kind": "bind_connection", "provider": name},
                                     src))
                # secret: expectations are covered by the util-header join, which is the
                # authority — re-deriving them here would be a second copy that can drift.
    return out


def _schedule_nodes(server: Any, cfg: RoutineConfig) -> list[dict]:
    """Does this routine's file say WHEN it actually runs?

    Two ways it can stop saying so, both silent. A LANE with a cron SUPPRESSES its members'
    own crons (D71), so a member that kept one has a file naming a time it will never fire at —
    `steward-hub-maintainer` recorded 23:00 while firing at 06:30 in its lane's chain, with
    nothing anywhere saying the two disagreed. The other way is the mirror: a routine with no
    cron of its own, in no scheduled lane, is never started by anything on a clock, which is a
    perfectly good on-demand design and indistinguishable from an oversight.

    Only the LANE is asked. A routine's domain shares a config block and a store but nothing on
    a clock; its tags fire nothing at all. Nothing on either axis can make this file disagree
    with itself (docs/lanes-domains.md).

    Neither breaks a run, so neither shouts. What they cost is the operator's belief about when
    the routine runs, which is exactly what a NOTE is for.
    """
    from .. import lanes as lanes_mod

    if not cfg.enabled:
        return []                       # a disabled routine already says it does not run
    from ..engine.stopping import goal_reached
    if goal_reached(cfg.dir):
        # Not a misconfiguration — the opposite. Every goal-scoped stopping condition is met, so
        # the scheduler stops firing it. Said out loud because the page would otherwise show a
        # cron that will never fire again with nothing explaining why.
        #
        # And said with NO fix, because nothing here is unmet: the row reports a success; an
        # offer to reopen the goal is an affordance for undoing one. Reopening is a decision a
        # person makes about the work, taken in the panel that owns the conditions (`_node`).
        return [_node("schedule:goal", "retired", NOTE,
                      "every final-goal stopping condition is met, so this routine is FINISHED "
                      "and nothing fires it any more",
                      "its schedule is inert and a lane chain skips it; the Decisions page "
                      "carries the proposal that retires it for good")]
    try:
        all_lanes = lanes_mod.list_lanes(server.routines_home)
    except OSError:
        return []
    # At most one can match — lane membership is exclusive (rsched.lanes) — but the scan is
    # over the store rather than a per-routine key, so it reads as a list either way.
    mine = [lane for lane in all_lanes if cfg.slug in lanes_mod.member_slugs(lane)]
    scheduling = next((lane for lane in mine if lane.get("cron")), None)
    if scheduling and cfg.cron:
        paused = " (currently PAUSED, so nothing fires at all)" if scheduling.get("paused") else ""
        # `source` carries the lane's id because the prose can only name it — a name is not
        # addressable and the operator's next step is that lane's row. surface-view.js groups
        # rows by `doc` / `utils` alone, so this one appears under "from this routine's own
        # config"; the FIX carries the same lane, which is what a link can be built from.
        return [_node("schedule:cron", "suppressed", NOTE,
                      f"its lane {scheduling['name']!r} carries a schedule, which suppresses "
                      "every member's own cron",
                      f"this file records {cfg.cron!r} while the routine actually fires with "
                      f"the lane at {scheduling['cron']!r}{paused}",
                      {"kind": "lane_schedule", "lane": scheduling["id"],
                       "name": scheduling["name"]}, {"lane": scheduling["id"]})]
    if not scheduling and not cfg.cron:
        how = ("a trigger, a hand fire, or another run's schedule_run" if cfg.triggers
               else "a hand fire from the console, or another run's schedule_run")
        return [_node("schedule:none", "unscheduled", NOTE,
                      "it has no cron of its own and no lane schedules it",
                      f"nothing on a clock starts this routine; it runs only on {how}",
                      {"kind": "set_schedule"})]
    return []


def _phase_nodes(server: Any, cfg: RoutineConfig) -> list[dict]:
    """Does `state/phase.json` record the phase under the key the engine reads?

    The composer reads it as `.get("phase")`; that value is what scopes a stopping
    condition to a stage. Routines that invented their own key wrote a file that LOOKS right
    and matches nothing: funscript-trainer recorded `lifecycle`, self-audit `state`,
    routine-improver an empty object. Nothing breaks — the digest dumps the whole object, so
    the run still reads it — but every stage-scoped condition silently never fires.

    Only said for a routine whose recipe actually TRACKS a phase — detected by the recipe
    naming `state/phase.json`, not by a `## Phases` heading: the routines that get this wrong
    are exactly the ones that describe their phase in prose (self-audit walks a state machine
    through it, routine-improver a step cursor) and have no such heading. A routine that never
    mentions the file is not missing anything.

    ABSENCE is only a gap once a run has COMPLETED without recording one —
    the composer reads a missing file as "likely the first run"; the run in flight right
    now has not had its chance yet. A CONVERSATION is skipped outright: the converse pattern
    declares a phase, but conversations.py never writes one to state/phase.json by design, so
    every reply would carry a boot note about a file that is correctly absent.
    """
    from ..paths import read_json

    routine_dir = Path(getattr(cfg, "dir", None) or server.routines_home / cfg.slug)
    conversations = getattr(server, "conversations_home", None)
    if conversations is not None:
        try:
            if routine_dir.resolve().parent == Path(conversations).resolve():
                return []
        except OSError:
            return []
    main = routine_dir / "main.md"
    if not main.is_file():
        return []
    recipe = main.read_text(encoding="utf-8", errors="replace")
    for stage in sorted((routine_dir / "stages").glob("*.md")):
        recipe += stage.read_text(encoding="utf-8", errors="replace")
    if "phase.json" not in recipe and "## Phases" not in recipe:
        return []
    raw = read_json(routine_dir / "state" / "phase.json")
    if raw is None:
        if not any((routine_dir / "runs").glob("*/result.md")):
            return []
        return [_node("state:phase", "absent", NOTE,
                      "its recipe declares phases but no completed run has recorded one",
                      "the digest reports no phase and any stage-scoped stopping condition "
                      "never matches; the next run that records a phase fixes it",
                      {"kind": "fix_phase", "expected": "phase"})]
    if not isinstance(raw, dict) or not str(raw.get("phase") or "").strip():
        found = ", ".join(sorted(raw)) if isinstance(raw, dict) and raw else "nothing"
        return [_node("state:phase", "mis-keyed", NOTE,
                      "state/phase.json does not record the phase under the `phase` key",
                      f"the engine reads `phase`; this file holds {found}. The digest still "
                      "shows the object, but stage-scoped stopping conditions never match",
                      {"kind": "fix_phase", "expected": "phase"})]
    return []


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


def _rank(node: dict) -> tuple[int, int]:
    """The merge order for two rows about one entity: worst severity first, then the row that
    offers an action LAST — see `_one_row_per_entity`.
    """
    return _ORDER[node["severity"]], 1 if node.get("fix") else 0


def _one_row_per_entity(nodes: list[dict]) -> list[dict]:
    """One row per entity, chosen by a STATED precedence rather than by the order the checks
    happened to run in.

    Two checks legitimately reach the same id: `action:write_recipe` is emitted once as the
    deliberate switch it is (no fix) and once as uncovered by any held doc (with one), both
    NOTE. Which of the two survives decides whether the panel offers to undo a routine set up
    exactly as intended — far too load-bearing to rest on which append comes first upstream,
    where nothing marks either line as the one that must not move.

    Worst severity wins. At equal severity the row with NO fix wins, which is the "not unmet ⇒
    no fix" rule of `_node` read at the merge: a fix-less row is by construction one reporting
    something that is not unmet. The same entity being unmet on another reading never makes an
    offer to undo the deliberate half correct.
    """
    best: dict[str, dict] = {}
    for n in nodes:
        prev = best.get(n["id"])
        if prev is None or _rank(n) < _rank(prev):
            best[n["id"]] = n
    return list(best.values())


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
    nodes += _schedule_nodes(server, cfg)
    nodes += _phase_nodes(server, cfg)

    # The library's `requires:` and the DOMAIN's shared capability block, both read once. The
    # util join below asks both — between them they decide where an absent util's drop can be
    # performed at all — as do the checks after it.
    lib_requires = grants_mod.read_library_requires(server.permissions_home)
    domain = _domain_capabilities(server, cfg)

    # -- the util-header join: what the RESERVED utils this routine holds actually need ------
    # Utils already declare their secrets and their private filesystem stores; this is the
    # first thing that reads those declarations on behalf of the routine holding the util.
    secret_needs: dict[str, list[str]] = {}
    fs_needs: list[tuple[str, str, str, str]] = []
    for name in _held_utils(cfg, catalog):
        if name not in by_name:
            nodes.append(_absent_util_node(cfg, lib_requires, name, domain))
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

    covered_utils = set(_held_utils(cfg, catalog))    # names AND everything a gated tag covers
    from ..grants import capabilities_for
    # The same mapping with NO doc applied — `capabilities_for` normalizes its base, so this is
    # what the live config means once the absent keys have their defaults. Comparing the raise
    # against it (rather than against the raw dict) is what keeps an unset key from reading as
    # a shortfall.
    normalized = capabilities_for([], lib_requires, base=dict(caps))
    for slug in cfg.permissions or []:
        req = lib_requires.get(slug) or {}
        missing = [a for a in req.get("actions") or [] if a not in (caps.get("actions") or [])]
        missing += [f"util:{u}" for u in req.get("utils") or [] if u not in covered_utils]
        # ...and every DIAL the doc requires. Asked by RAISING the live mapping through the one
        # cascade instead of key by key: this check was written when `requires:` named actions
        # and utils only, so every dial added since — `runs`, `workflows`, `util_tags`,
        # `reminders` — fell straight through the guard whose whole job was to catch a held doc
        # the capabilities do not honor. The adopt path had the identical blindness and shipped
        # `reminders` "on by default" to zero of 32 routines with nothing anywhere to say so.
        # A key the raise CHANGES is a key the live value sits below; a key it leaves alone is
        # satisfied, including by a value above what the doc asks for. A dial added tomorrow
        # lands here on its own, because the cascade returns it.
        raised = capabilities_for([slug], lib_requires, base=dict(caps))
        missing += [f"{k}={raised[k]}" for k in raised
                    if k not in ("actions", "utils") and raised[k] != normalized[k]]
        if missing:
            # The same list twice, once for each audience: `effect` says what it costs, `fix`
            # hands the exact switches over so the offer can read "switch on runs=last"
            # rather than "go and look".
            nodes.append(_node(f"permission:{slug}", "unsatisfied", BLOCKS,
                               "held, but its requires: are not switched on",
                               "enforcement reads capabilities only, so it fails closed: "
                               + ", ".join(missing),
                               {"kind": "switch_on", "entity": slug, "missing": missing},
                               {"doc": slug}))

    nodes += _uncovered_nodes(cfg, lib_requires, domain)
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
