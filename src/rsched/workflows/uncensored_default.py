"""The `uncensored` role a NEWLY CREATED routine is born with — read from this instance,
never hardcoded.

R3680. Every library settings pattern ships `models: {}` as boilerplate, so a routine created
OUTSIDE a pattern — or from one whose `models` block nobody fills — was born with no uncensored
role at all. `endpoints.for_uncensored` has no `system_model` fallback, so on a classifier
refusal `engine/degrade._handle_refusal` had no uncensored target to re-drive the turn on: it
advanced the catalog fallback chain (more censored models, likely to refuse the same content)
and once that was exhausted ended the run. `feeld-android` was the one routine of 30 in that
state, and grepping for a `models:` key does NOT find the case — the key is present and empty.

Why the fix sits at CREATION and not in `for_uncensored`: an unset role returning None means
"referral is deliberately off", and that reading is load-bearing — `config/routine_scrub.py`
treats it as a legitimate value, the routine page's blank picker is labelled with it
(docs/architecture.md), and `docs/endpoints.md` sells takeover as opt-in. Defaulting it in the
engine would switch referral on for every routine whose role is unset on purpose, invisibly. A
value WRITTEN into `routine.yaml` is the opposite: it shows on the routine's page, `rolecatalog`
checks it like any other role, and the operator can clear it.

What the standard IS gets read off this instance in two steps, so no model name is written twice
in the tree:
  1. the value the FLEET already agrees on — the most common `models.uncensored` among the
     routines that exist, kept only if this instance's catalog still serves it (a stale name
     would reproduce F643, where a routine died at boot resolving its own role);
  2. failing that — a fresh instance with no routines yet — the catalog's SOLE model whose name
     or provider id says it has no guardrails. Sole, because choosing among several would be
     this module inventing the operator's policy.
Neither answers → "" and nothing is written, exactly the behaviour that shipped before.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from ..config import ServerConfig
from ..paths import read_yaml

#: Words providers put in the NAME or the provider model id of a model with its guardrails
#: removed. Consulted ONLY when the fleet names no standard (step 2 above).
_MARKERS = ("uncensored", "abliterated", "unfiltered")


def _fleet_values(routines_home: Path) -> list[str]:
    """Every existing routine's `models.uncensored`, in slug order. Best-effort per file: one
    that is unreadable, is not YAML, or does not parse to a mapping is passed over — reading
    the fleet must never be what stops a routine being created.
    """
    found: list[str] = []
    for path in sorted(Path(routines_home).glob("*/routine.yaml")):
        try:
            cfg = read_yaml(path, {})
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(cfg, dict):
            continue
        models = cfg.get("models")
        name = models.get("uncensored") if isinstance(models, dict) else None
        if isinstance(name, str) and name.strip():
            found.append(name.strip())
    return found


def _sole_catalog_candidate(server: ServerConfig) -> str:
    """The catalog's one model that says it has no guardrails, or "" when there are none or
    more than one.
    """
    hits = sorted(name for name, mc in (server.models or {}).items()
                  if any(word in f"{name} {mc.model}".lower() for word in _MARKERS))
    return hits[0] if len(hits) == 1 else ""


def fleet_standard(server: ServerConfig) -> str:
    """The catalog model NAME a new routine's `models.uncensored` should hold on this instance,
    or "" when the instance says nothing.

    Ties are broken alphabetically so two instances with the same fleet answer the same, and a
    value the catalog does not carry is never returned: writing one would hand the new routine
    a role that dies on resolution instead of the refusal recovery it is there for.
    """
    catalog = set(server.models or {})
    if not catalog:
        return ""
    counts = Counter(name for name in _fleet_values(server.routines_home) if name in catalog)
    if not counts:
        return _sole_catalog_candidate(server)
    most = max(counts.values())
    return min(name for name, count in counts.items() if count == most)


def with_uncensored(models: dict, server: ServerConfig) -> dict:
    """`models` with the instance's uncensored standard filled in when it names none.

    A role already named — by the pattern, by the caller, by the person designing the routine —
    is never touched: this fills a GAP, it does not impose a choice.
    """
    out = dict(models or {})
    if not str(out.get("uncensored") or "").strip() and (standard := fleet_standard(server)):
        out["uncensored"] = standard
    return out
