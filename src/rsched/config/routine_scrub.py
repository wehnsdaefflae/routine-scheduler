"""What `load_routine` does to the bindings and limits a routine.yaml names: each unusable entry
is REPORTED and dropped, so a run starts on the field's default instead of on a guess.

Split out of `routine.py` (one responsibility per file): reading a file into a model and
judging what the model's mappings say are different jobs. Every check here keeps the loader's
one rule — best-effort recovery, with a problem line naming what was ignored and why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    DEFAULT_BUDGETS,
    DEFAULT_LADDER,
    EFFORT_LEVELS,
    LADDER_KEYS,
    MODEL_KINDS,
    ROUTINE_MODEL_ROLES,
)
from .trialconf import trial_load_problems

if TYPE_CHECKING:
    from .routine import RoutineConfig


def _scrub_models(cfg: RoutineConfig) -> list[str]:
    """Model roles the engine does not know, and efforts that are not a level of a chat role."""
    problems = []
    for kind in [k for k in cfg.models if k not in ROUTINE_MODEL_ROLES]:
        hint = (" — the subroutine role is retired: children run the routine's MAIN model "
                "by default (a call overrides per child); remove this key"
                if kind == "subroutine" else "")
        problems.append(f"models.{kind}: unknown model kind "
                        f"(expected one of {ROUTINE_MODEL_ROLES}){hint}")
        del cfg.models[kind]
    for role in [r for r, level in cfg.efforts.items()
                 if r not in MODEL_KINDS or level not in EFFORT_LEVELS]:
        problems.append(f"efforts.{role}: expected a chat role ({', '.join(MODEL_KINDS)}) set to "
                        f"one of {', '.join(EFFORT_LEVELS)} (ignored)")
        del cfg.efforts[role]
    problems += _orphan_uncensored_effort(cfg)
    return problems


def _orphan_uncensored_effort(cfg: RoutineConfig) -> list[str]:
    """`efforts.uncensored` with no `models.uncensored` — the shape a LOST role leaves behind.

    A `models:` PATCH replaces the mapping wholesale, which is what makes "clear a role"
    expressible at all (`web/patch_bindings.py`), so an edit that re-sends the map without one
    key CLEARS that role — and nothing anywhere says so. Twice in the seven days to 2026-11-06 a
    console `models` edit dropped `models.uncensored` while leaving `efforts.uncensored` behind:
    `self-audit` ran that way for THREE DAYS, `routine-improver` for 27 minutes, and in both
    cases the only reason anyone found out is that a config audit happened to resolve the role by
    hand (config-optimizer, R3227/R3228). `load_routine` reported no problem, no health event was
    filed, and `EndpointRegistry.for_uncensored` returning `None` is a legitimate value meaning
    "referral off", so the engine cannot tell a deliberate off from a loss.

    The consequence is a FAILED run, not a degradation: on a classifier refusal with no
    uncensored target the fallback chain advances, every catalog model on this instance carries
    `fallbacks: []`, and `engine/degrade.py` raises (`_switch_to_fallback` → None).

    Narrowed to `uncensored` DELIBERATELY. `endpoints.for_model` falls back to the instance
    `system_model`, so an `efforts.main` or `efforts.tool_call` with no model of its own is
    legitimate and six routines on this instance are in exactly that state; `for_uncensored` has
    no fallback at all. An orphan effort for a role that still resolves is not evidence of
    anything, and a check that fires on it would be noise on six routines from its first day.

    Reported, never dropped: the effort is correct again the moment the role is restored, and
    deleting it would erase the only trace that the role was ever configured.
    """
    if "uncensored" not in cfg.efforts or cfg.models.get("uncensored"):
        return []
    return [("efforts.uncensored is set to "
             f"{cfg.efforts['uncensored']!r} but models.uncensored names no model — the role "
             "does not resolve, so a classifier refusal has no uncensored target to refer to "
             "and the run FAILS rather than degrading. A `models:` edit REPLACES the whole map, "
             "so re-sending it without this key clears the role: name a model for it, or "
             "remove efforts.uncensored to say the referral is deliberately off")]


def _scrub_ladder(cfg: RoutineConfig) -> list[str]:
    """The ladder block: unknown keys dropped, defaults filled in, two values type-checked."""
    problems = []
    for key in [k for k in cfg.ladder if k not in LADDER_KEYS]:
        problems.append(f"ladder.{key}: unknown ladder key (expected one of "
                        f"{sorted(LADDER_KEYS)})")
        del cfg.ladder[key]
    cfg.ladder = {**DEFAULT_LADDER, **cfg.ladder}
    if not isinstance(cfg.ladder["enabled"], bool):
        problems.append(f"ladder.enabled: expected true or false, got "
                        f"{cfg.ladder['enabled']!r} (ignored)")
        cfg.ladder["enabled"] = DEFAULT_LADDER["enabled"]
    height = cfg.ladder["rung_height"]
    if not (isinstance(height, int) and not isinstance(height, bool) and height > 0):
        problems.append(f"ladder.rung_height: expected a whole number above zero, got "
                        f"{height!r} (ignored)")
        cfg.ladder["rung_height"] = DEFAULT_LADDER["rung_height"]
    return problems


def scrub_bindings(cfg: RoutineConfig, raw: dict) -> list[str]:
    """Models, efforts, the trial, connections, budgets, the stale `deliberation` key and the
    ladder — every problem line, with the unusable entries already removed from `cfg`.
    """
    problems = _scrub_models(cfg)
    problems += trial_load_problems(raw.get("trial"), cfg.trial)
    from ..oauth.providers import PROVIDERS  # function-level: oauth imports secrets, not config
    for prov in [p for p in cfg.connections if p not in PROVIDERS]:
        problems.append(
            f"connections.{prov}: unknown provider (expected one of {sorted(PROVIDERS)})")
        del cfg.connections[prov]
    for key in [k for k in cfg.budgets if k not in DEFAULT_BUDGETS]:
        problems.append(f"budgets.{key}: unknown budget")
        del cfg.budgets[key]
    # deliberation lives in TUNING, never in config — a routine.yaml key is stale data
    if "deliberation" in raw:
        problems.append("deliberation: belongs in tuning.yaml (machine-tunable behavior) "
                        "— the routine.yaml key is ignored")
    return problems + _scrub_ladder(cfg)
