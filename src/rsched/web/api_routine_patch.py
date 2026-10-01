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

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .. import rules as rules_mod
from .. import schedule
from ..config import DELIBERATION_LEVELS, write_tuning
from ..config.base import _known_tz
from ..config.routine import RunGateConfig, RunGatePatch
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

#: The longest Steward-hub heading a routine may name — a heading, not a sentence.
HUB_TAB_MAX = 60


class SchedulePatch(BaseModel):
    """The `schedule` a PATCH may carry, typed for the same reason `BudgetsPatch` is (R102).

    It was a free mapping merged verbatim into routine.yaml's `schedule:`, so a raw `cron`
    croniter rejects — or a zone zoneinfo does not know — was written as given and answered
    `updated: ["schedule"]`, and the next load DROPPED it with a problem (`_validate_lenient`):
    a scheduled routine silently became a manual one. A misspelled key landed beside the real
    ones, read by nothing; a non-mapping `friendly` was a 500. All of those are 422s here —
    strict, like `RunGatePatch`, so `"catchup": 1` is refused rather than coerced. Switching a
    routine off is not a schedule key: it is the top-level `enabled`, which the friendly
    "disabled" cadence writes.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    friendly: dict | None = None    # {"frequency": …} — translated to cron + the server's tz
    catchup: Literal["skip", "run_once"] | None = None
    cron: str | None = None
    tz: str | None = None

    # an explicit null reaches an after-validator too; it means "not sent" (exclude_none)
    @field_validator("tz")
    @classmethod
    def _tz_known(cls, v: str | None) -> str | None:
        return v if v is None else _known_tz(v)

    @field_validator("cron")
    @classmethod
    def _cron_parses(cls, v: str | None) -> str | None:
        if v and v.strip() and not croniter.is_valid(v):
            raise ValueError(f"not a cron expression: {v!r}")
        return v


class RoutinePatch(BaseModel):
    # forbid unknown keys: this is the validated single-writer save path — a misspelled
    # field silently dropped reads as "saved" to a direct API caller (and to the Decisions
    # page's config-patch apply, which verifies its keys against `updated`); a 422 names
    # the stray instead.
    model_config = ConfigDict(extra="forbid")

    # the off switch (RoutineConfig.enabled) — strict, so `"no"` is a 422 rather than read as
    # true: the dashboard's pause and goal retirement send it, and the friendly "disabled"
    # cadence writes the same key
    enabled: StrictBool | None = None
    run_gate: RunGatePatch | None = None    # partial — validated MERGED, see _apply_run_gate
    schedule: SchedulePatch | None = None   # {"friendly":…, "catchup":…} (cron built server-side)
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
    hub_tab: str | None = Field(default=None, max_length=HUB_TAB_MAX)  # Steward-hub heading —
    #                                          trimmed; `""` names none (docs/status-pages.md)
    permissions: list[str] | None = None    # held conduct-doc slugs — REPLACE wholesale, routed
    #                                          to the canonical two-layer resolve (D132/F482)
    capabilities: dict | None = None        # the capabilities mapping under those permissions —
    #                                          raised to cover held docs' requires, floored back
    rules: list[str] | None = None          # general-rule slugs this routine practises — REPLACE
    #                                          wholesale, validated against the library
    #                                          (rules.apply_changes)
    improve: bool | None = None             # include in the routine-improver's passes (default on)
    pattern: str | None = None              # the library settings pattern it follows — a
    #                                          reference only; `""` follows none (docs/patterns.md)
    shared_reminders: list[str] | None = None  # the library's shared reminders this routine
    #                                          reads (ids) — REPLACE wholesale
    deliberation: str | None = None         # DELIBERATION_LEVELS — how much thinking lands on paper
    keep_runs: int | None = None            # retention.keep_runs — how many run dirs to keep
    fs_read_roots: list[str] | None = None  # dirs the run may READ beyond its own
    fs_write_roots: list[str] | None = None  # dirs the run may WRITE (one covering the routine

    @field_validator("hub_tab", mode="before")
    @classmethod
    def _trim_hub_tab(cls, v: object) -> object:
        # trimmed BEFORE the length check, so surrounding whitespace never costs a 422
        return v.strip() if isinstance(v, str) else v


def _apply_run_gate(raw: dict, updates: dict) -> None:
    """Merge a partial `run_gate` into the stored one and validate the RESULT.

    A partial patch is the normal case — the console toggles `enabled` without resending the
    checks — so the body alone cannot be judged: `{enabled: true}` is sound over a stored check
    list and unsound over none. The merge is validated as a whole and written as a whole; `checks`
    REPLACES the stored list, like every list this endpoint takes. Pops the key, so the generic
    merge below never sees it.
    """
    stored = raw.get("run_gate")
    merged = {**(stored if isinstance(stored, dict) else {}), **updates.pop("run_gate")}
    try:
        raw["run_gate"] = RunGateConfig.model_validate(merged).model_dump()
    except ValueError as exc:
        raise HTTPException(422, f"run_gate: {exc}") from exc


def _apply_pattern_fields(server, raw: dict, updates: dict) -> None:
    """`pattern` names the library settings pattern this routine follows — a REFERENCE: the
    routine's values stay its own, the pattern is what the page reads them against. An unknown
    slug is a 400; `""` follows none and removes the key, so "no pattern" has one spelling.
    `shared_reminders` are the library's shared reminders it reads, each an id the store holds.
    Pops both keys.
    """
    from .. import reminders as rem
    from ..patterns import store

    if "pattern" in updates:
        want = (updates.pop("pattern") or "").strip()
        if not want:
            raw.pop("pattern", None)
        elif store.read(server.libraries_home, want) is None:
            raise HTTPException(400, f"no settings pattern {want!r} in the library")
        else:
            raw["pattern"] = want
    if "shared_reminders" in updates:
        want_ids = [str(r) for r in updates.pop("shared_reminders") or []]
        known = {r.id for r in rem.load_global(server.reminders_home)}
        if unknown := [r for r in want_ids if r not in known]:
            raise HTTPException(400, f"no shared reminder {', '.join(unknown)} in the library")
        if want_ids:
            raw["shared_reminders"] = sorted(set(want_ids))
        else:
            raw.pop("shared_reminders", None)


def _apply_rules_field(rules_home: Path, routine_dir: Path, raw: dict, updates: dict) -> None:
    """Bind/unbind the routine's general rules from a PATCH `rules` list — REPLACE wholesale,
    validated against the library. Shares the ONE canonical path (rules.apply_changes) with the
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
    capabilities mapping is a 422, and the mapping is RAISED to cover every held doc's requires
    and FLOORED back to them (D8). Letting these fall through to the generic top-level merge
    instead would write an unvalidated `permissions:` list and a capabilities mapping that
    could contradict it — authority granted by a key nobody cascaded. Sharing the writer with
    the editor is what makes that true of BOTH doors.

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

    `enabled` needs nothing here: the generic merge writes it where every reader looks. The
    friendly cadence is the other door to the same key — its "disabled" choice switches the
    routine off and any real cadence switches it back on — unless the patch names `enabled`
    itself, which the merge then writes over it.
    """
    if "keep_runs" in updates:
        n = updates.pop("keep_runs")
        if not isinstance(n, int) or n < 1:
            raise HTTPException(400, "keep_runs must be a positive integer")
        raw.setdefault("retention", {})["keep_runs"] = n
    for roots_key in ("fs_read_roots", "fs_write_roots"):
        if roots_key in updates:
            # absolute, deduplicated, and never a credential store (SEC-1) — the one
            # enforcer every grant edge calls (config_fields.validate_roots)
            updates[roots_key] = validate_roots(roots_key, updates[roots_key])
    if "schedule" in updates:
        # typed by SchedulePatch: catchup, a raw cron and its tz arrive valid
        sched_patch = updates.pop("schedule") or {}
        raw.setdefault("schedule", {})
        if "friendly" in sched_patch:
            friendly = sched_patch.pop("friendly")
            try:
                cron = schedule.friendly_to_cron(friendly)
            except (ValueError, TypeError) as exc:     # a field of the wrong type is a 400 too
                raise HTTPException(400, f"invalid schedule: {exc}") from exc
            raw["schedule"].update(cron=cron, tz=schedule.server_tz())
            raw["enabled"] = friendly.get("frequency") != "disabled"
        # merge the remaining RAW keys (cron / tz / catchup) — a friendly spec was already
        # translated and popped above; tz is preserved when only cron is sent.
        raw["schedule"].update(sched_patch)

@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    updates = patch.model_dump(exclude_none=True)
    if patch.run_gate is not None:
        updates["run_gate"] = patch.run_gate.model_dump(exclude_unset=True)
    return apply_updates(request, info, updates)


def apply_updates(request: Request, info, updates: dict, *, message: str = "") -> dict:
    """Apply already-validated PATCH fields to a routine's `routine.yaml` — the one writer the
    PATCH route and the settings page's single "accept changes" both go through, so a value
    lands the same way whichever door it came in by. `updates` has RoutinePatch's shape.

    ALL OR NOTHING: every field is judged before anything is written. Two of them write
    outside the one final save — the rule binder (`rules.apply_changes`) and the tuning file
    — and both used to write first, so a patch refused over a LATER field (an unknown pattern,
    a bad cron) had already rebound the routine's rules or re-levelled its deliberation:
    uncommitted, unscanned, unannounced to a live run, under a 4xx that said nothing landed.
    """
    # No busy-guard (D35): pure routine.yaml config, read at run START only — saving
    # mid-run applies at the next run. Destructive ops (archive) keep their guard.
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    signal_values = dict(updates)
    # `updated` reports every field this PATCH applied. Captured BEFORE the appliers pop
    # what they consume (models/connections/machines/grants/keep_runs/schedule) — the
    # response and commit message must not under-report, because the Decisions page's
    # config-patch apply verifies its patch keys against this list (R102: a key an
    # endpoint silently ignores must read as NOT applied, never as success).
    requested = list(updates)
    # deliberation is TUNING, not config — it lands in tuning.yaml (recipe-classed), never in
    # routine.yaml (the user's sealed authority surface). Judged FIRST, so a tuning-only patch
    # returns without rewriting routine.yaml; in a mixed patch it is written only once every
    # other field has passed.
    level = updates.pop("deliberation", None)
    if level is not None:
        if level not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level {level!r} "
                                     f"(expected one of {DELIBERATION_LEVELS})")
        if not updates:
            write_tuning(info.cfg.dir, {"deliberation": level})
            _git_commit(request, info.cfg.dir, "tuning.yaml edit via web (deliberation)")
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
    # D132/F482: the two authority keys route to the permissions surface's own resolver rather
    # than the generic merge below — a decision may now propose them, and what lands is what the
    # editor would have written.
    _apply_permissions_fields(request, info, raw, updates)
    _apply_pattern_fields(_state(request).server, raw, updates)
    if "run_gate" in updates:
        _apply_run_gate(raw, updates)
    _apply_resource_fields(raw, updates)
    if "tags" in updates:
        raw["tags"] = clean_tags(updates.pop("tags"))
    if "hub_tab" in updates and not updates["hub_tab"]:
        updates.pop("hub_tab")
        raw.pop("hub_tab", None)    # `""` names no heading; absence is its one spelling
    # Rules bind/unbind through the ONE canonical path (rules.apply_changes) — F392: a
    # config_patch carrying `rules` now applies through the generic PATCH, not only the
    # dedicated /routines/{slug}/rules picker. LAST of the appliers, because it is the one
    # that writes: it refuses an unknown slug before writing, and nothing after it refuses.
    _apply_rules_field(_state(request).server.rules_home, info.cfg.dir, raw, updates)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    if level is not None:
        write_tuning(info.cfg.dir, {"deliberation": level})
    # F337 rides in the shared writer: a run already in flight booted its policy, schema and
    # prompt from the OLD config, and is told what changed and which half reaches it now.
    live = write_routine_config(
        request, info, raw,
        message=message or f"routine.yaml edit via web ({', '.join(requested)})",
        fields=requested, values=signal_values)
    return {"ok": True, "updated": requested, **({"told_live_run": True} if live else {})}
