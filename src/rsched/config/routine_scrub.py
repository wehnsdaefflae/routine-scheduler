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
    return problems


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
    depth = cfg.ladder["max_depth"]
    if not (isinstance(depth, int) and not isinstance(depth, bool) and depth > 0):
        problems.append(f"ladder.max_depth: expected a whole number above zero, got "
                        f"{depth!r} (ignored)")
        cfg.ladder["max_depth"] = DEFAULT_LADDER["max_depth"]
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
