"""`PATCH /routines/{slug}` — the one validated writer of a routine's config.

Split out of `api_routine_edit.py` (F393). A routine never writes its own `routine.yaml`, so
this is the single path config changes take, and it carries the whole burden of that: per-field
validation, `extra="forbid"` so a misspelled key is a 422 rather than a silent drop, an
`updated` list the Decisions page verifies its patch against, and (F337) the signal that tells a
LIVE run what changed and which half of it reaches it now.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import domains, entities, schedule
from .. import rules as rules_mod
from ..config import DELIBERATION_LEVELS, write_tuning
from ..config.routine import RunGateConfig
from ..paths import read_yaml
from .config_fields import (
    BudgetsPatch,
    clean_tags,
    validate_connections,
    validate_machines,
    validate_models,
    validate_roots,
)
from .routines_common import (
    _git_commit,
    _info,
    _state,
    signal_config_change,
    write_routine_config,
)

router = APIRouter(tags=["routine-patch"])


class RoutinePatch(BaseModel):
    # forbid unknown keys: this is the validated single-writer save path — a misspelled
    # field silently dropped reads as "saved" to a direct API caller (and to the Decisions
    # page's config-patch apply, which verifies its keys against `updated`); a 422 names
    # the stray instead.
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    run_gate: RunGateConfig | None = None
    schedule: dict | None = None            # {"friendly":…, "catchup":…} (cron built server-side)
    budgets: BudgetsPatch | None = None     # the runaway backstops, by name — a misspelled
    #                                          key is a 422 here, never a silent revert at load
    models: dict | None = None              # {main|tool_call|uncensored: catalog name}
    connections: dict | None = None         # {provider: account-label} OAuth connection bindings
    grants: dict | None = None              # {entity-id: bool} decision rows (secret exposure
    #                                          + deny-forever tombstones — entities.py)
    machines: list[str] | None = None       # catalog machine names this routine may act on (SSH)
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None           # freeform filter tags (e.g. ["meta"])
    domain: str | None = None               # the shared surface this routine is part of — at
    #                                          most one, `""` leaves (docs/lanes-domains.md)
    permissions: list[str] | None = None    # held conduct-doc slugs — REPLACE wholesale, routed
    #                                          to the canonical two-layer resolve (D132/F482)
    capabilities: dict | None = None        # the capabilities mapping under those permissions —
    #                                          raised to cover held docs' requires, floored back
    rules: list[str] | None = None          # general-rule slugs this routine practises — REPLACE
    #                                          wholesale, validated against the library, main.md's
    #                                          derived practices tail resynced (rules.apply_changes)
    improve: bool | None = None             # include in the routine-improver's passes (default on)
    output_compression: Literal["off", "measure", "compress"] | None = None
    deliberation: str | None = None         # DELIBERATION_LEVELS — how much thinking lands on paper
    keep_runs: int | None = None            # retention.keep_runs — how many run dirs to keep
    fs_read_roots: list[str] | None = None  # dirs the run may READ beyond its own
    fs_write_roots: list[str] | None = None  # dirs the run may WRITE (one covering the routine

def _apply_rules_field(rules_home: Path, routine_dir: Path, raw: dict, updates: dict) -> None:
    """Bind/unbind the routine's general rules from a PATCH `rules` list — REPLACE wholesale,
    validated against the library, with main.md's derived `## Standing practices` tail
    resynced. Shares the ONE canonical path (rules.apply_changes) with the
    /routines/{slug}/rules picker so a config_patch carrying `rules` (a Decisions-page
    `approve & apply` for a rule-binding decision) applies through the generic PATCH too —
    before F392 the key hit RoutinePatch's extra=forbid and 422'd invisibly. Pops `rules`;
    an unknown slug is a legible 400. Next-run semantics like every other field here.
    """
    if "rules" not in updates:
        return
    want = updates.pop("rules") or []
    if not isinstance(want, list) or any(not isinstance(r, str) for r in want):
        raise HTTPException(400, "rules: must be a list of rule slugs")
    held = rules_mod.current_rules(routine_dir)
    add = [r for r in want if r not in held]
    remove = [r for r in held if r not in want]
    try:
        rules_mod.apply_changes(rules_home, routine_dir, add, remove)
    except KeyError as exc:
        raise HTTPException(400, f"unknown rule: {exc.args[0]!r}") from exc
    raw["rules"] = rules_mod.current_rules(routine_dir)

def _apply_domain_field(routines_home: Path, raw: dict, updates: dict) -> None:
    """Join or leave the shared surface this routine names in its OWN routine.yaml (`domain:`).

    Membership lives here and nowhere else, which is what makes "at most one domain" a fact of
    the file rather than a rule someone has to enforce across a list — `GET /api/domains` reads
    its members back out of the routines (docs/lanes-domains.md). An unknown id is a 400, not a
    save: the picker only offers ids that exist; a routine pointing at a domain that does not
    would inherit nothing while reading everywhere as "in a domain". Leaving is `""`, which
    REMOVES the key, so "in no domain" keeps its single spelling — absence.

    Next-run semantics (`configflow.CLASSIFICATION`): the shared block is merged when the
    routine is loaded and the store is injected into the fs roots at boot, so a live run keeps
    the surface it booted with and is told the field changed.
    """
    if "domain" not in updates:
        return
    want = (updates.pop("domain") or "").strip()
    if not want:
        raw.pop("domain", None)
        return
    if domains.get(routines_home, want) is None:
        raise HTTPException(400, f"unknown domain {want!r} (create it on the Routines page)")
    raw["domain"] = want

def _apply_permissions_fields(request: Request, info, raw: dict, updates: dict) -> None:
    """Apply a PATCH's `permissions` / `capabilities` through the ONE canonical path — the
    same two-layer resolve the dedicated `PUT /routines/{slug}/permissions` editor uses
    (D132/F482).

    A decision could propose every other part of a routine's config and not this one: the
    two authority keys were absent from `RoutinePatch`, so a config_patch carrying them hit
    `extra="forbid"` and 422'd — the operator was told to go and click the editor himself.
    The operator settled that on 2026-09-15: a config_patch MAY carry them, routed to the
    permissions surface, with the honesty gate kept.

    Routing, not merging, is the whole point. These two keys are the authority surface, and
    `write_permission_layers` is what makes them safe: unknown doc slugs are dropped, a junk
    capabilities mapping is a 422, the mapping is RAISED to cover every held doc's requires and
    FLOORED back to them (D8), and what lands in the file is only what this routine OWNS —
    its DOMAIN's docs, list entries and dials are left to the domain (D82). Letting these fall
    through to the generic top-level merge instead would write an unvalidated `permissions:`
    list and a capabilities mapping that could contradict it — authority granted by a key
    nobody cascaded. Sharing the writer with the editor is what makes that true of BOTH doors:
    this path used to run the resolve and skip the strip, so a patch naming only a confirm
    dial wrote the whole domain-unioned set into the member's file, where a member's own key
    always wins — the domain never reached that routine again (F489 through the second door).

    REPLACE wholesale, exactly like the editor: `permissions` is the held-doc set, not an
    addition to it, because that is what the one canonical resolver means by `active`. A patch
    naming only `capabilities` keeps the routine's current docs; one naming only `permissions`
    re-floors the existing mapping against the new set. Pops both keys, so the caller's
    catch-all merge never sees them.
    """
    if "permissions" not in updates and "capabilities" not in updates:
        return
    from .api_routine_edit import PermissionsBody, write_permission_layers

    want_docs = updates.pop("permissions", None)
    want_caps = updates.pop("capabilities", None)
    if want_docs is None:
        want_docs = list(info.cfg.permissions or [])
    if not isinstance(want_docs, list) or any(not isinstance(p, str) for p in want_docs):
        raise HTTPException(400, "permissions: must be a list of permission-doc slugs")
    if want_caps is not None and not isinstance(want_caps, dict):
        raise HTTPException(400, "capabilities: must be a mapping")
    body = PermissionsBody(active=want_docs, capabilities=want_caps)
    write_permission_layers(_state(request).server, info, body, raw)


def _apply_resource_fields(raw: dict, updates: dict) -> None:
    """Place the routine.yaml resource fields a PATCH carries that the caller's generic
    top-level merge can't handle on its own: retention.keep_runs (nested under `retention:`),
    the fs roots (validated, stripped — left in `updates` for the wholesale merge), and the
    schedule (a friendly spec → cron + the server's tz, plus the catchup policy). Pops what
    it consumes. A write root covering the routine's own dir unlocks recipe self-editing
    (grants.py) — the user's deliberate choice here, the same lever the routine-improver holds.
    """
    if "keep_runs" in updates:
        n = updates.pop("keep_runs")
        if not isinstance(n, int) or n < 1:
            raise HTTPException(400, "keep_runs must be a positive integer")
        raw.setdefault("retention", {})["keep_runs"] = n
    for roots_key in ("fs_read_roots", "fs_write_roots"):
        if roots_key in updates:
            updates[roots_key] = validate_roots(roots_key, updates[roots_key])
            # The never-grantable guard, at the edge where the grant is MADE. It existed only
            # on the runtime ask path (engine/availability.py), so "never grantable, to any
            # routine, by design" was true of what a run asked for and false of what an
            # operator typed into the Filesystem-roots panel — which is how the instance's
            # credential dir became a live read+write root on a routine (SEC-1). A refusal
            # here, naming the path, is the difference between a promise and a seal.
            if guarded := entities.guarded_roots(updates[roots_key]):
                raise HTTPException(
                    400, f"{roots_key}: {', '.join(guarded)} {entities.GUARDED_ROOT_REASON}")
    # F448: `enabled` is the OLD spelling of "does this routine fire", kept because the
    # dashboard's D72 start/pause toggle PATCHes it. The firing gate reads `schedule.disabled`
    # alone, so without this the key fell through the generic merge below and wrote a bare
    # `enabled:` nothing reads — while `updated` still reported it applied (R102: a key an
    # endpoint silently ignores must never read as success). Translate it at the edge; the
    # rest of the codebase knows only `schedule.disabled`.
    if "enabled" in updates:
        on = updates.pop("enabled")
        if not isinstance(on, bool):
            raise HTTPException(400, "enabled must be a boolean")
        raw.setdefault("schedule", {})["disabled"] = not on
        raw.pop("enabled", None)
    if "schedule" in updates:
        sched_patch = updates.pop("schedule") or {}
        raw.setdefault("schedule", {})
        if "disabled" in sched_patch:
            if not isinstance(sched_patch["disabled"], bool):
                raise HTTPException(400, "schedule.disabled must be a boolean")
            raw.pop("enabled", None)
        if "friendly" in sched_patch:
            try:
                friendly = sched_patch.pop("friendly")
                cron = schedule.friendly_to_cron(friendly)
                raw["schedule"]["disabled"] = friendly.get("frequency") == "disabled"
                raw.pop("enabled", None)
            except ValueError as exc:
                raise HTTPException(400, f"invalid schedule: {exc}") from exc
            raw["schedule"].update(cron=cron, tz=schedule.server_tz())
        if sched_patch.get("catchup") not in (None, "skip", "run_once"):
            raise HTTPException(400, "catchup must be 'skip' or 'run_once'")
        # merge any remaining RAW keys (cron / tz / catchup) verbatim — a friendly spec was
        # already translated and popped above; tz is preserved when only cron is sent.
        raw["schedule"].update(sched_patch)

@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    # No busy-guard (D35): pure routine.yaml config, read at run START only — saving
    # mid-run applies at the next run. Destructive ops (archive) keep their guard.
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    updates = patch.model_dump(exclude_none=True)
    if patch.run_gate is not None:
        updates["run_gate"] = patch.run_gate.model_dump(exclude_unset=True)
    signal_values = dict(updates)
    # `updated` reports every field this PATCH applied. Captured BEFORE the appliers pop
    # what they consume (models/connections/machines/grants/keep_runs/schedule) — the
    # response and commit message must not under-report, because the Decisions page's
    # config-patch apply verifies its patch keys against this list (R102: a key an
    # endpoint silently ignores must read as NOT applied, never as success).
    requested = list(updates)
    # deliberation is TUNING, not config — it lands in tuning.yaml (recipe-classed), never in
    # routine.yaml (the user's sealed authority surface). Handle it FIRST, before any raw
    # mutation, so a tuning-only patch returns without rewriting routine.yaml.
    if "deliberation" in updates:
        level = updates.pop("deliberation")
        if level not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level {level!r} "
                                     f"(expected one of {DELIBERATION_LEVELS})")
        write_tuning(info.cfg.dir, {"deliberation": level})
        if not updates:
            _git_commit(info.cfg.dir, "tuning.yaml edit via web (deliberation)")
            _state(request).scheduler.rescan()
            live = signal_config_change(info, ["deliberation"], {"deliberation": level})
            return {"ok": True, "updated": ["deliberation"],
                    **({"told_live_run": True} if live else {})}
    # Validate per-routine models: known kinds, each a catalog model NAME. Models REPLACE
    # wholesale (not merge) so blanking a kind clears it back to the system_model fallback.
    if "models" in updates:
        raw["models"] = validate_models(_state(request).server, updates.pop("models"))
    # Validate connection bindings: known provider, non-empty account label; REPLACE wholesale
    # (blanking a provider clears it). Existence of the connection is NOT required — a routine may
    # bind ahead of connecting; the engine injects nothing until the account is connected.
    if "connections" in updates:
        raw["connections"] = validate_connections(updates.pop("connections"))
    # Validate machine bindings: each a name in the instance catalog; REPLACE wholesale (an empty
    # list clears them). Unlike connections, we DO require catalog membership — a machine name is
    # meaningless off the catalog, and the picker only offers catalog names.
    if "machines" in updates:
        raw["machines"] = validate_machines(_state(request).server, updates.pop("machines"))
    # Validate the grant-decision rows (entities.py ids → bool); REPLACE wholesale —
    # removing a row returns that entity to undecided (asked on first use / requestable).
    if "grants" in updates:
        from ..entities import normalize_grants
        gmap, gproblems = normalize_grants(updates.pop("grants") or {})
        if gproblems:
            raise HTTPException(400, "; ".join(gproblems))
        raw["grants"] = gmap
    # Rules bind/unbind through the ONE canonical path (rules.apply_changes) — F392: a
    # config_patch carrying `rules` now applies through the generic PATCH, not only the
    # dedicated /routines/{slug}/rules picker. Extracted to a helper to keep this handler
    # under the branch-complexity budget.
    _apply_rules_field(_state(request).server.rules_home, info.cfg.dir, raw, updates)
    # D132/F482: the two authority keys route to the permissions surface's own resolver rather
    # than the generic merge below — a decision may now propose them, and what lands is what the
    # editor would have written.
    _apply_permissions_fields(request, info, raw, updates)
    _apply_domain_field(_state(request).server.routines_home, raw, updates)
    _apply_resource_fields(raw, updates)
    if "tags" in updates:
        raw["tags"] = clean_tags(updates.pop("tags"))
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    # F337 rides in the shared writer: a run already in flight booted its policy, schema and
    # prompt from the OLD config, and is told what changed and which half reaches it now.
    live = write_routine_config(request, info, raw,
                                message=f"routine.yaml edit via web ({', '.join(requested)})",
                                fields=requested, values=signal_values)
    return {"ok": True, "updated": requested, **({"told_live_run": True} if live else {})}


class AdoptTemplate(BaseModel):
    template: str          # the library template slug to copy in ("" is rejected — say what)


@router.post("/routines/{slug}/adopt-template")
def adopt_template(request: Request, slug: str, body: AdoptTemplate) -> dict:
    """Copy a settings template's values into this routine's `routine.yaml`, once.

    A template is a PRESELECTION, not a layer (2026-08-30). Nothing about this write is
    remembered: afterwards the routine's file says what the routine IS, every value is editable
    in the panel that owns it, and removing one is removing it — there is no `template_except:`
    because there is nothing left to subtract from. Adopting twice is harmless (the merge is a
    union that never overwrites), and adopting a second template ADDS to the first.

    Returns what the write contributed, so an adoption that changed nine things says so.
    """
    from ..templates import adopt_into, read_template

    info = _info(request, slug)
    server = _state(request).server
    tpl = read_template(server.libraries_home, body.template.strip())
    if tpl is None:
        raise HTTPException(404, f"no settings template {body.template!r} in the library")
    raw = read_yaml(info.cfg.dir / "routine.yaml", {})
    merged, added = adopt_into(raw, tpl["config"])
    if not added:
        return {"ok": True, "template": tpl["slug"], "added": [],
                "note": "this routine already has everything the template supplies"}
    # An adoption can change nine fields at once, `budgets` and `grants` among them — both
    # LIVE-classified — so it goes through the same writer as every other save and a live run
    # is told, instead of quietly running the rest of its turn under the old values.
    live = write_routine_config(request, info, merged,
                                message=f"adopt settings template {tpl['slug']} via web",
                                fields=added, values=tpl["config"])
    return {"ok": True, "template": tpl["slug"], "added": added,
            **({"told_live_run": True} if live else {})}
