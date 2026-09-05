"""`PATCH /routines/{slug}` — the one validated writer of a routine's config.

Split out of `api_routine_edit.py` (F393). A routine never writes its own `routine.yaml`, so
this is the single path config changes take, and it carries the whole burden of that: per-field
validation, `extra="forbid"` so a misspelled key is a 422 rather than a silent drop, an
`updated` list the Decisions page verifies its patch against, and (F337) the signal that tells a
LIVE run what changed and which half of it reaches it now.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import domains, schedule
from .. import rules as rules_mod
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, write_tuning
from ..paths import atomic_write_yaml, read_yaml
from .routines_common import (
    _git_commit,
    _info,
    _state,
    signal_config_change,
)

router = APIRouter(tags=["routine-patch"])


class RoutinePatch(BaseModel):
    # forbid unknown keys: this is the validated single-writer save path — a misspelled
    # field silently dropped reads as "saved" to a direct API caller (and to the Decisions
    # page's config-patch apply, which verifies its keys against `updated`); a 422 names
    # the stray instead.
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    schedule: dict | None = None            # {"friendly":…, "catchup":…} (cron built server-side)
    budgets: dict | None = None
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
    rules: list[str] | None = None          # general-rule slugs this routine practises — REPLACE
    #                                          wholesale, validated against the library, main.md's
    #                                          derived practices tail resynced (rules.apply_changes)
    improve: bool | None = None             # include in the routine-improver's passes (default on)
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
            vals = updates[roots_key] or []
            if not isinstance(vals, list) or any(not isinstance(p, str) or not p.strip()
                                                 for p in vals):
                raise HTTPException(400, f"{roots_key}: must be a list of non-empty path strings")
            updates[roots_key] = [p.strip() for p in vals]
    if "schedule" in updates:
        sched_patch = updates.pop("schedule") or {}
        raw.setdefault("schedule", {})
        if "friendly" in sched_patch:
            try:
                cron = schedule.friendly_to_cron(sched_patch.pop("friendly"))
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
        server = _state(request).server
        for kind, name in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(
                    400, f"unknown model kind {kind!r} (expected one of {MODEL_KINDS})")
            if not isinstance(name, str) or name not in server.models:
                raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        raw["models"] = updates.pop("models")
    # Validate connection bindings: known provider, non-empty account label; REPLACE wholesale
    # (blanking a provider clears it). Existence of the connection is NOT required — a routine may
    # bind ahead of connecting; the engine injects nothing until the account is connected.
    if "connections" in updates:
        from ..oauth.providers import PROVIDERS
        for prov, account in (updates["connections"] or {}).items():
            if prov not in PROVIDERS:
                raise HTTPException(400, f"unknown connection provider {prov!r}")
            if not isinstance(account, str) or not account:
                raise HTTPException(400, f"connections.{prov}: must be an account label")
        raw["connections"] = updates.pop("connections")
    # Validate machine bindings: each a name in the instance catalog; REPLACE wholesale (an empty
    # list clears them). Unlike connections, we DO require catalog membership — a machine name is
    # meaningless off the catalog, and the picker only offers catalog names.
    if "machines" in updates:
        catalog = _state(request).server.machines
        names = updates["machines"] or []
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise HTTPException(400, "machines: must be a list of catalog machine names")
        for n in names:
            if n not in catalog:
                raise HTTPException(400, f"unknown machine {n!r} (add it in Settings → Machines)")
        raw["machines"] = updates.pop("machines")
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
    _apply_domain_field(_state(request).server.routines_home, raw, updates)
    _apply_resource_fields(raw, updates)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    atomic_write_yaml(path, raw)
    _git_commit(info.cfg.dir, f"routine.yaml edit via web ({', '.join(requested)})")
    _state(request).scheduler.rescan()
    # F337: a run already in flight booted its policy, schema and prompt from the OLD config.
    # Tell it what changed and which half of it reaches it now — the drift this closes is that
    # some fields silently did and most silently did not.
    live = signal_config_change(info, requested, patch.model_dump(exclude_none=True))
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
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    merged, added = adopt_into(raw, tpl["config"])
    if not added:
        return {"ok": True, "template": tpl["slug"], "added": [],
                "note": "this routine already has everything the template supplies"}
    atomic_write_yaml(path, merged)
    _git_commit(info.cfg.dir, f"adopt settings template {tpl['slug']} via web")
    _state(request).scheduler.rescan()
    return {"ok": True, "template": tpl["slug"], "added": added}
