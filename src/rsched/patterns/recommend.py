"""The RECOMMENDER — which pattern fits a routine and what THIS routine needs beyond it.

Two questions, asked at two moments. At creation: the new routine is saved with its pattern's
values; everything specific to it — the folders it reads, the mailbox its gate watches, the
finish line the person described — becomes PENDING changes the routine page shows under "check
the changes i recommend." On the page: "Recommend for this routine" asks the same second
question of a routine that already exists.

A recommendation is a proposal and never a write (`drafts.py`), so the person decides every
change with one "accept changes". It is also never trusted as it arrives: every proposed value
goes through validation before it is offered; one that does not validate is dropped rather than
offered — a proposal the accept button would refuse is not a proposal.

How far that reaches is `NO_SEMANTIC_CHECK`, and it is NOT yet the whole accept route. Every
proposed field is checked for SHAPE by the patch model; the fields in `semantic_checks()` /
`whole_field_checks()` are then checked for MEANING with the accept's own validators. The rest
are offered on their shape alone — and for 13 of them a validator exists on the accept route
that this module does not consult, so a value the SYSTEM generated can still be refused on a
page the person never typed into (R2884). Each one is named there with its reason; the accept
is whole-draft, so one refused field 400s every other change the person kept.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from . import fields, store

log = logging.getLogger("rsched.patterns.recommend")

#: Fields a recommendation may not propose: a trigger's webhook is a token only the server
#: mints; the name and description are the routine's own.
_NOT_PROPOSED = ("triggers", "name", "description")

#: The two checks that read an instance CATALOG. Without a server their semantic half is
#: skipped rather than guessed — a missing catalog is not evidence the value is bad.
NEEDS_CATALOGS = frozenset({"machines", "models"})

#: Every governable field with NO semantic check in `semantic_checks()` /
#: `whole_field_checks()`, and the DECISION taken for it. The `runrecord.BEHAVIOUR` /
#: `NOT_BEHAVIOUR` idiom: each `fields.GOVERNABLE` key is in exactly one of the three, so a new
#: governable field forces a choice (tests/test_recommend_coverage.py fails on one in none of
#: them) instead of joining a silent majority that is offered untested.
#:
#: A reason opening `GAP —` means the accept route DOES judge this field's meaning and the
#: recommender does not consult that judgement: the value can be offered and then refused, and
#: because the accept is whole-draft it takes every other kept change down with it. Those are
#: measured, named defects awaiting their own increments — not fields that need no check. The
#: test treats the set as a RATCHET: an entry may be fixed away, a new one may not appear.
NO_SEMANTIC_CHECK: dict[str, str] = {
    # --- no semantic check EXISTS: the patch model's shape is the whole judgement ----------
    "budgets": "`BudgetsPatch`/`_check_budgets` is the whole check — a name `DEFAULT_BUDGETS` "
               "knows, an integer (-1 = unlimited); the accept adds nothing beyond it",
    "improve": "a bare bool on `RoutinePatch`, written by the generic merge; no meaning to judge",
    "permissions": "the accept never refuses one: unknown doc slugs are silently DROPPED "
                   "(`resolve_permission_layers`), and 'a list of strings' is the patch model's "
                   "own check. A proposal can land PARTIALLY — that is R2314's other half, and "
                   "a loudness problem (the dropped-proposal increment), not a validity one",
    "tags": "`clean_tags` strips and drops blanks and raises nothing — unrefusable by design",
    "triggers": "never proposed at all: `_NOT_PROPOSED` drops it before validation, because a "
                "trigger's webhook is a token only the server mints",
    # --- GAP: a validator exists on the accept route and the recommender never calls it -----
    "capabilities": "GAP — `grants.normalize_capabilities` (via `write_permission_layers`) 422s "
                    "an unknown key, a non-list layer or a confirm/runs/reminders level off its "
                    "ladder; the patch model only checks `dict`. One of the two values the "
                    "operator actually hit (R2884)",
    "deliberation": "GAP — `_validate_tuning` 400s a level outside `DELIBERATION_LEVELS`; the "
                    "patch model types it `str`. The other value the operator actually hit",
    "fs_read_roots": "GAP — `config_fields.validate_roots` 400s a non-absolute path and a "
                     "credential store (SEC-1); the patch model checks `list[str]` only",
    "fs_write_roots": "GAP — the same enforcer, the same unconsulted 400 (R2314: the roots could "
                      "not be set through the UI at all)",
    "grants": "GAP — `entities.normalize_grants` 400s an unknown entity id. `ask_first`, so it "
              "needs the person's click anyway, but a refused row still 400s the whole accept",
    "keep_runs": "GAP — `_apply_resource_fields` 400s anything but a positive integer, a floor "
                 "the patch model's `int` does not carry",
    "ladder": "GAP — `_apply_ladder` 400s an unknown key, a non-bool `enabled` and a "
              "`rung_height` under 1, judged on the block MERGED with the stored one",
    "ladder_turn_interval": "GAP — `_validate_tuning` 400s anything under "
                            "`ladder.MIN_OVERSIGHT_TURNS`",
    "oversight_turns": "GAP — the same floor, with 0 meaning DERIVE it (never below it)",
    "reminders": "GAP — `_apply_pattern_fields` 400s a shared-reminder id the library does not "
                 "hold; nothing checks library membership here",
    "rules": "GAP — `rules.apply_changes` 400s an unknown rule slug; nothing checks the library",
    "run_gate": "GAP — `_apply_run_gate` 422s the gate doc MERGED with the stored one, so the "
                "proposal alone cannot be judged without merging it the same way first",
    "schedule": "GAP — `schedule.friendly_to_cron` 400s a friendly spec it cannot translate; "
                "`SchedulePatch` types the mapping without checking the frequency vocabulary",
}

CHOICE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pattern", "why"],
    "properties": {"pattern": {"type": "string"}, "why": {"type": "string"}},
}

CHANGES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["changes"],
    "properties": {"changes": {
        "type": "array",
        "items": {"type": "object", "additionalProperties": False,
                  "required": ["field", "value_json", "reason"],
                  "properties": {
                      "field": {"type": "string"},
                      "value_json": {"type": "string",
                                     "description": "the field's COMPLETE new value as JSON"},
                      "reason": {"type": "string",
                                 "description": "one sentence the person reads beside it"}}},
    }},
}


def choose(server, *, workflow: str, name: str, task: str) -> str:
    """The pattern a routine on `workflow` follows: the one built on that workflow when there is
    exactly one, else the system model's pick among all of them. "" only when the library holds
    no pattern at all.
    """
    patterns = store.list_all(Path(server.libraries_home))
    if not patterns:
        return ""
    on_workflow = [p for p in patterns if p["workflow"] == workflow]
    if len(on_workflow) == 1:
        return on_workflow[0]["slug"]
    pool = on_workflow or patterns
    from ..workflows.suggest import _ask_json

    listing = "\n".join(f"- {p['slug']}: {p['summary']} WHEN: {p['when']}" for p in pool)
    obj, _why = _ask_json(server, (
        "Pick the settings pattern this recurring LLM-agent routine should follow. Answer with "
        "one slug from the list.\n\n"
        f"ROUTINE: {name}\nTASK:\n{task[:6000]}\n\nPATTERNS:\n{listing}"),
        CHOICE_SCHEMA, purpose=f"choose pattern → {name}")
    picked = str((obj or {}).get("pattern") or "")
    return picked if any(p["slug"] == picked for p in pool) else pool[0]["slug"]


def recommend(server, *, slug: str, saved: dict, pattern: dict | None,
              context: str) -> dict[str, dict]:
    """`{field: {value, reason}}` — what this routine needs that it does not hold now. Empty
    when the system model is unavailable or proposes nothing that validates.
    """
    from ..workflows.suggest import _ask_json

    allowed = [k for k in fields.GOVERNABLE if k not in _NOT_PROPOSED]
    prompt = _prompt(server, slug, saved, pattern, context, allowed)
    obj, why = _ask_json(server, prompt, CHANGES_SCHEMA, purpose=f"recommend settings → {slug}")
    if obj is None:
        log.warning("recommend(%s): no recommendation (%s)", slug, why)
        return {}
    out: dict[str, dict] = {}
    for row in obj.get("changes") or []:
        key = str(row.get("field") or "")
        if key not in allowed:
            continue
        try:
            value = json.loads(str(row.get("value_json") or "null"))
        except ValueError:
            continue
        if fields.equal(key, saved.get(key), value) or not valid(key, value, server):
            continue
        out[key] = {"value": fields.canonical(key, value),
                    "reason": str(row.get("reason") or "").strip()[:400]}
    return out


def valid(key: str, value: object, server: object | None = None) -> bool:
    """Would the page's own accept take this value? Checked with the validators it uses.

    The patch model checks a field's SHAPE; the fields in `semantic_checks()` are then checked
    for MEANING by the accept route's own validators (`web/config_fields.py`, called from
    `api_routine_patch.apply_updates`), and a value that clears the shape and fails the meaning
    is the worst kind to offer: the accept is whole-draft, so one refused field 400s every other
    change the person kept. That is how `connections: {"fau-mail": {"scopes": [...]}}` — a
    util's name where an OAuth provider belongs — cost ewsan-bohrdaten-steward all nine of its
    proposed settings. So run those validators here too. `models` and `machines` need the
    instance catalogs (`NEEDS_CATALOGS`); without a server their semantic half is skipped rather
    than guessed. A field in `whole_field_checks()` answers for itself INSTEAD of the pair —
    `finish_line` is not a patch field at all. Every other governable field clears on shape
    alone: `NO_SEMANTIC_CHECK` says which, and which of them the accept would still refuse.
    """
    from pydantic import ValidationError

    if whole := whole_field_checks().get(key):
        return whole(value)
    from ..web.api_routine_patch import RoutinePatch

    pkey, pvalue = fields.patch_shape(key, value, lane_managed=False)
    try:
        RoutinePatch.model_validate({pkey: pvalue})
    except (ValidationError, ValueError, TypeError):
        return False
    return not _semantic_problem(key, value, server)


def semantic_checks(server: object | None = None) -> dict[str, Callable[[object], object]]:
    """Governable field → the accept route's OWN semantic validator for it, bound to `server`
    and raising `HTTPException` when the accept would refuse the value.

    THE COVERED SET, AS DATA: `_semantic_problem` dispatches on this table and
    tests/test_recommend_coverage.py reads its keys, so what the recommender checks is read
    from the code that does the checking. A hand-kept second list of "the fields we check"
    would agree with itself while the coverage drifted — which is the defect this table exists
    to stop (R2884). Called with no server it still names its keys.
    """
    from ..web import config_fields

    return {
        "connections": lambda value: config_fields.validate_connections(
            value if isinstance(value, dict) else {}),
        "efforts": lambda value: config_fields.validate_efforts(
            value if isinstance(value, dict) else {}),
        "machines": lambda value: config_fields.validate_machines(
            server, value if isinstance(value, list) else []),
        "models": lambda value: config_fields.validate_models(
            server, value if isinstance(value, dict) else {}),
    }


def whole_field_checks() -> dict[str, Callable[[object], bool]]:
    """Governable field → a check that answers for the WHOLE field, INSTEAD of the shape-then-
    meaning pair the other fields go through — also part of the covered set, read from code.

    `finish_line` is not a `RoutinePatch` field at all: it is written through its own owner
    (`engine/finishline.py`), so the patch model would refuse it outright and its own validator
    is the only one that applies.
    """
    from ..engine import finishline

    def finish_line_ok(value: object) -> bool:
        raw = value if isinstance(value, dict) else {}
        return not finishline.problems(finishline.normalize(raw)) and all(
            isinstance(o, dict) and o.get("judge") in finishline.JUDGES
            for o in raw.get("outcomes") or [])

    return {"finish_line": finish_line_ok}


def _semantic_problem(key: str, value: object, server: object | None) -> bool:
    """True when the accept route's own validator would refuse this shaped-correct value.

    Dispatches on `semantic_checks()`; a field that table does not name is not checked here,
    and `NO_SEMANTIC_CHECK` holds the decision taken for each one.
    """
    from fastapi import HTTPException

    try:
        check = semantic_checks(server).get(key)
        if check is not None and not (server is None and key in NEEDS_CATALOGS):
            check(value)
    except HTTPException as exc:
        log.info("recommend: dropping %s — the accept would refuse it (%s)", key, exc.detail)
        return True
    except Exception:  # a validator that cannot run is not evidence the value is bad
        log.warning("recommend: could not semantically check %s", key, exc_info=True)
    return False


def _prompt(server, slug: str, saved: dict, pattern: dict | None, context: str,
            allowed: list[str]) -> str:
    from .. import gatekit, library_docs
    from ..oauth.providers import PROVIDERS

    providers = ", ".join(sorted(PROVIDERS)) or "none configured"
    perms = "\n".join(f"- {d['slug']}: {d['summary']}"
                      for d in library_docs.list_docs(server.permissions_home))
    rules = "\n".join(f"- {d['slug']}: {d['summary']}"
                      for d in library_docs.list_docs(server.rules_home))
    # Each parameter with its type and meaning, as the console's gate form shows them: a name
    # alone left the model to guess formats — and the one weekday numbering this prompt spells
    # is the SCHEDULE's (0 = Sunday), while a `weekdays` check counts from Monday.
    kinds = "\n".join(
        f"- {kind}: {meaning}; params: " + ("; ".join(
            f"{name} ({typ}{', required' if required else ''}): {help_}"
            for name, (typ, required, help_) in spec.items()) or "none")
        for kind, (meaning, spec) in gatekit.KINDS.items())
    base = (f"It follows the pattern {pattern['slug']!r} ({pattern['summary']})."
            if pattern else "It follows no pattern yet.")
    return (
        "You configure a recurring LLM-agent routine. Propose ONLY the settings this routine "
        "needs that its current values lack — the things specific to it: the folders it "
        "reads or writes, the machine or account it uses, its cadence, a run gate that skips a "
        "scheduled fire ONLY when there is certainly nothing to do (a check that cannot tell "
        "counts as work; every gate should carry a max_quiet backstop), a finish line when "
        "its job ends, extra permissions or rules its work needs. Propose nothing the task does "
        "not call for, never widen access 'just in case', and leave a value alone when you are "
        "unsure. Each change carries the field's COMPLETE new value as JSON.\n\n"
        f"ROUTINE {slug}. {base}\n\nWHAT IT IS FOR:\n{context[:12000]}\n\n"
        f"ITS CURRENT SETTINGS (JSON):\n{json.dumps(saved, ensure_ascii=False)[:12000]}\n\n"
        f"FIELDS YOU MAY CHANGE: {', '.join(allowed)}.\n"
        'finish_line is {"outcomes": [{"text", "judge": "run"|"you"|"date", '
        '"date": "YYYY-MM-DD" (date only)}], "until": "YYYY-MM-DD" or ""}. '
        'run_gate is {"enabled", "timeout_s", "checks": [{"id", "kind", …params}]}. '
        'schedule is {"friendly": {"frequency": "hourly"|"daily"|"weekly"|'
        '"monthly"|"manual", "time": "HH:MM", "weekdays": [0-6, 0=Sunday]}, '
        '"catchup": "skip"|"run_once"}.\n'
        f'connections is {{provider: "account-label"}} and names an OAUTH provider only — '
        f'one of {providers}. A mailbox or an API the routine reaches through a UTIL is not a '
        'connection: that access is a permission or a secret, never a connections entry.\n\n'
        f"GATE CHECK KINDS:\n{kinds}\n\nPERMISSIONS:\n{perms}\n\nGENERAL RULES:\n{rules}")


def at_creation(server, *, slug: str, pattern_slug: str, context: str,
                finish_line: dict | None) -> list[str]:
    """After a routine is scaffolded on its pattern: write its pending changes — the finish
    line the person described, the pattern's `ask_first` values (which creation never writes),
    plus whatever the recommender finds — under the message the routine page leads with.
    Returns the fields proposed.
    """
    from ..config import load_routine
    from . import drafts

    cfg, _problems = load_routine(Path(server.routines_home) / slug)
    if cfg is None:
        return []
    saved = fields.snapshot(cfg)
    pattern = store.read(Path(server.libraries_home), pattern_slug) if pattern_slug else None
    changes = recommend(server, slug=slug, saved=saved, pattern=pattern, context=context)
    if pattern is not None:
        reason = f"the value {pattern['title']} carries — it decides access, so it waits for you"
        changes.update({key: {"value": value, "reason": reason}
                        for key, value in pattern["settings"].items()
                        if fields.BY_KEY[key].ask_first and key not in changes
                        and not fields.equal(key, saved.get(key), value)
                        and valid(key, value, server)})
    if finish_line and (finish_line.get("outcomes") or finish_line.get("until")) \
            and valid("finish_line", finish_line):
        changes["finish_line"] = {"value": fields.canonical("finish_line", finish_line),
                                  "reason": "the finish line you described while designing it"}
    if changes:
        drafts.write(Path(server.routines_home), slug, changes=changes, pattern=None,
                     message=CHECK_MESSAGE, source="creation")
    return sorted(changes)


CHECK_MESSAGE = "check the changes i recommend."
