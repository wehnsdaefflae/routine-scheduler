"""The settings VOCABULARY JUDGE — would the page's own accept take this value, and if not, why?

ONE judge, reached by every WRITER of a proposed setting (F610). The recommender validated what
the system model proposed and nothing else did, so the other writers — "follow this pattern",
the pattern-diff half of "Recommend for this routine", a pattern stored in the library — put
values into a routine's pending changes that the accept route refuses, and the refusal arrived
on the operator's click. The accept is whole-draft, so one refused field 400s every other change
the person kept: that is how `connections: {"fau-mail": {"scopes": [...]}}` — a util's name where
an OAuth provider belongs — cost ewsan-bohrdaten-steward all nine of its proposed settings, and
how `budgets: {"max_minutes": 900}` (a budget this repo has never had) cost four clicks and a
hand-typed toast.

The check belongs to the writer, not to each caller: `drafts.write` and `store.create` call
`problems()` here, so a call site added later is validated by construction rather than by
whoever remembers. `problem()` returns the SENTENCE the writer refuses with — it names the
offending key and the vocabulary that was allowed instead, because a refusal that teaches
nothing is a defect in this project.

How a field is judged, in the order tried:

* not a setting, or not governable — the vocabulary itself is wrong; `fields` is the list.
* a field with an owner of its own (`_WHOLE`): `triggers` and `finish_line` are not fields of
  `RoutinePatch` at all (the accept routes them to `api_hooks.reconciled_triggers` and
  `engine/finishline.py`), so judging them by the patch model would refuse every value —
  including the `triggers: []` and empty `finish_line` that all 14 SHIPPED patterns carry. A
  guard that blocks the operator is worse than the defect it closes.
* everything else: SHAPE by `RoutinePatch`, then MEANING by the accept route's own validators
  (`semantic_checks`). `NO_SEMANTIC_CHECK` names every governable field with no semantic check
  and the decision taken for it; a `GAP —` reason there means the accept DOES judge that
  field's meaning and this module does not consult it yet (R2884), which is a measured defect
  awaiting its own increment rather than a field that needs no check.

A value needing instance state — a catalog model name, a machine — is judged only when a
`server` is passed (`NEEDS_CATALOGS`); without one its semantic half is SKIPPED rather than
guessed, because a missing catalog is not evidence the value is bad, and a filing check stricter
than the accept would refuse a proposal that works.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import fields

log = logging.getLogger("rsched.patterns.validate")

#: The two checks that read an instance CATALOG. Without a server their semantic half is
#: skipped rather than guessed — a missing catalog is not evidence the value is bad.
NEEDS_CATALOGS = frozenset({"machines", "models"})

#: Every governable field with NO semantic check in `semantic_checks()` /
#: `whole_field_checks()`, and the DECISION taken for it. The `runrecord.BEHAVIOUR` /
#: `NOT_BEHAVIOUR` idiom: each `fields.GOVERNABLE` key is in exactly one of the three, so a new
#: governable field forces a choice (tests/test_recommend_coverage.py fails on one in none of
#: them) instead of joining a silent majority that is offered untested.
#:
#: A reason opening `GAP —` means the accept route DOES judge this field's meaning and this
#: module does not consult that judgement: the value can be offered and then refused, and
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
    # --- GAP: a validator exists on the accept route and this module never calls it ---------
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


def valid(key: str, value: object, server: object | None = None) -> bool:
    """Would the page's own accept take this value? `problem()` says why not."""
    return not problem(key, value, server)


def problem(key: str, value: object, server: object | None = None) -> str:
    """"" when the accept would take `key: value`, else the sentence a writer refuses with —
    the offending key, what is wrong with it, and the vocabulary that was allowed instead.
    """
    if key not in fields.BY_KEY:
        return (f"{key!r} is not a setting — a proposal may carry "
                f"{', '.join(fields.GOVERNABLE)}.")
    if not fields.BY_KEY[key].governable:
        return (f"{key}: a proposal never carries a routine's own {key} — it is what makes one "
                f"routine this routine. A proposal may carry "
                f"{', '.join(fields.GOVERNABLE)}.")
    if whole := whole_field_checks().get(key):
        return whole(value)
    return _shape_problem(key, value) or _semantic_problem(key, value, server)


def problems(settings: object, server: object | None = None) -> list[str]:
    """Every reason this settings mapping could not be proposed as it stands, one sentence per
    offending field. Empty when every value would survive the accept.

    Takes `object`, not `dict`: the callers hand over a document read from a library YAML file
    or a proposal built by a model, so "not a mapping at all" is one of the answers this has to
    be able to give rather than a shape it may assume.
    """
    if not isinstance(settings, dict):
        return ["settings is a mapping of field → value"]
    return [why for key in sorted(settings)
            if (why := problem(key, settings[key], server))]


def _shape_problem(key: str, value: object) -> str:
    """Why `RoutinePatch` — the one validated writer of a routine's config — would refuse this
    value's SHAPE, named in the settings vocabulary the person reads rather than the patch's.
    """
    from pydantic import ValidationError

    from ..web.api_routine_patch import RoutinePatch

    try:
        pkey, pvalue = fields.patch_shape(key, value, lane_managed=False)
    except (ValueError, TypeError, AttributeError) as exc:
        return f"{key}: {exc}"
    try:
        RoutinePatch.model_validate({pkey: pvalue})
    except ValidationError as exc:
        return "; ".join(_rows(key, exc)) or f"{key}: the settings patch model refuses it"
    except (ValueError, TypeError) as exc:
        return f"{key}: {exc}"
    return ""


def _rows(key: str, exc) -> list[str]:
    """One readable sentence per pydantic error, led by the SETTINGS key — `budgets` rather
    than the patch spelling, and `reminders` rather than `shared_reminders`.
    """
    out = []
    for e in exc.errors():
        loc = [str(p) for p in (e.get("loc") or [])][1:]
        msg = str(e.get("msg") or "").removeprefix("Value error, ")
        out.append(f"{key}.{'.'.join(loc)}: {msg}" if loc else f"{key}: {msg}")
    return out


def semantic_checks(server: object | None = None) -> dict[str, Callable[[object], object]]:
    """Governable field → the accept route's OWN semantic validator for it, bound to `server`
    and raising `HTTPException` when the accept would refuse the value.

    THE COVERED SET, AS DATA: `_semantic_problem` dispatches on this table and
    tests/test_recommend_coverage.py reads its keys, so what is checked is read from the code
    that does the checking. A hand-kept second list of "the fields we check" would agree with
    itself while the coverage drifted — which is the defect this table exists to stop (R2884).
    Called with no server it still names its keys.
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


def whole_field_checks() -> dict[str, Callable[[object], str]]:
    """Governable field → a check that answers for the WHOLE field, INSTEAD of the shape-then-
    meaning pair the other fields go through — also part of the covered set, read from code.
    Each returns the refusal sentence, or "" when the value stands.

    Neither of these is a `RoutinePatch` field: the accept routes `finish_line` to
    `engine/finishline.py` and `triggers` to `api_hooks.reconciled_triggers`, so the patch model
    would refuse every value either one can hold — including the `triggers: []` and empty finish
    line every shipped pattern carries. Their own owners are the only judges that apply.
    """
    return {"finish_line": _finish_line_problem, "triggers": _triggers_problem}


def _finish_line_problem(value: object) -> str:
    """The finish line's own owner judging it (`engine/finishline.py`), which is also what the
    settings page's PUT uses — so both doors refuse the same documents.
    """
    from ..engine import finishline

    if value is not None and not isinstance(value, dict):
        return ('finish_line: a finish line is {"outcomes": [{"text", "judge", "date"}], '
                '"until": "YYYY-MM-DD"} — not a ' + type(value).__name__)
    raw = value if isinstance(value, dict) else {}
    try:
        norm = finishline.normalize(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        return f"finish_line: {exc}"
    if found := finishline.problems(norm):
        return "finish_line: " + "; ".join(str(f) for f in found)
    strays = [o for o in (raw.get("outcomes") or [])
              if not (isinstance(o, dict) and o.get("judge") in finishline.JUDGES)]
    if strays:
        return (f"finish_line: every outcome names its judge — one of "
                f"{', '.join(finishline.JUDGES)}")
    return ""


def _triggers_problem(value: object) -> str:
    """The trigger list's own owner judging it: `reconciled_triggers` against an EMPTY current
    list, which is the hardest reading — every row in the value has to be creatable. It writes
    nothing (identity is minted in memory and thrown away), which is why it can be asked here.
    """
    from fastapi import HTTPException

    from ..web.api_hooks import reconciled_triggers

    if value is not None and not isinstance(value, (list, tuple)):
        return "triggers: a trigger list is a list of {type, cooldown_s, max_fires_per_day}"
    try:
        reconciled_triggers([], list(value or []))
    except HTTPException as exc:
        return str(exc.detail)
    except (ValueError, TypeError, KeyError) as exc:
        return f"triggers: {exc}"
    return ""


def _semantic_problem(key: str, value: object, server: object | None) -> str:
    """Why the accept route's own validator would refuse this shaped-correct value, or "".

    Dispatches on `semantic_checks()`; a field that table does not name is not checked here,
    and `NO_SEMANTIC_CHECK` holds the decision taken for each one. A validator that cannot RUN
    is not evidence the value is bad — it is logged and the value stands, because a check
    stricter than the accept would refuse a proposal that works.
    """
    from fastapi import HTTPException

    try:
        check = semantic_checks(server).get(key)
        if check is None or (server is None and key in NEEDS_CATALOGS):
            return ""
        check(value)
    except HTTPException as exc:
        return str(exc.detail)
    except Exception:
        log.warning("validate: could not semantically check %s", key, exc_info=True)
    return ""
