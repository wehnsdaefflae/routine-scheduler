"""The resource BINDINGS a routine PATCH may set — models, efforts, connections, machines and a
model trial — validated and written into the raw routine.yaml mapping.

Split out of `api_routine_patch.py` (one responsibility per file): each key here is a REPLACE
wholesale against the catalogs the instance holds, and each is popped out of `updates` so the
generic merge that follows never sees it.
"""

from __future__ import annotations

from .config_fields import (
    validate_connections,
    validate_efforts,
    validate_machines,
    validate_models,
    validate_trial,
)


def apply_bindings(server, raw: dict, updates: dict) -> None:
    """Validate and write every binding key present in `updates` into `raw`, popping it."""
    # Models: known kinds, each a catalog model NAME. REPLACE wholesale (not merge) so blanking a
    # kind clears it back to the system_model fallback. Efforts are their own setting beside it.
    if "models" in updates:
        raw["models"] = validate_models(server, updates.pop("models"))
    if "efforts" in updates:
        raw["efforts"] = validate_efforts(updates.pop("efforts"))
    # Connections: known provider, non-empty account label; REPLACE wholesale (blanking a
    # provider clears it). Existence of the connection is NOT required — a routine may bind
    # ahead of connecting; the engine injects nothing until the account is connected.
    if "connections" in updates:
        raw["connections"] = validate_connections(updates.pop("connections"))
    # Machines: each a name in the instance catalog; REPLACE wholesale (an empty list clears
    # them). Unlike connections, we DO require catalog membership — a machine name is
    # meaningless off the catalog, and the picker only offers catalog names.
    if "machines" in updates:
        raw["machines"] = validate_machines(server, updates.pop("machines"))
    # A MODEL TRIAL: every model it names meets the checks a `models:` binding meets, because
    # for its runs it IS one. Replaced whole (the generic merge would blend two trials); null
    # removes the key, so "no trial" has one spelling.
    if "trial" in updates:
        trial = updates.pop("trial")
        if trial is None:
            raw.pop("trial", None)
        else:
            raw["trial"] = validate_trial(server, trial)
