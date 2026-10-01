"""BUILDING a `GrantPolicy` from config.

Split out of `grantpolicy.py` (F393): the policy OBJECT answers questions — and keeps the two
path predicates it asks itself (`is_recipe_path`, `is_runs_path`), since a module that imports
the policy cannot also be the one the policy imports — while this reads the routine's
capabilities and the library's `requires:` into one.
"""

from __future__ import annotations

from pathlib import Path

from .grantpolicy import GrantPolicy
from .grants import (
    GATED_KINDS,
    normalize_capabilities,
    read_library_requires,
    split_util_verb,
)


def load_policy(permissions_home: Path, active: list[str] | None,
                capabilities: dict | None = None, current_run_ts: str = "",
                recipe_unlocked: bool = False, admin: bool = False,
                grants_map: dict | None = None) -> GrantPolicy:
    """Build the run policy from the routine's OWN capabilities mapping; the library's
    `requires:` declarations contribute only the reserved-util vocabulary and the
    capability→doc index that lets denials name the covering permission. `active` (the
    held conduct docs) is carried for the composer's prose — it unlocks nothing here.
    `grants_map` (routine.yaml `grants:`) contributes the deny-forever tombstones; its
    true rows (secret exposure) are read by the secrets gate, not here.
    """
    lib = read_library_requires(permissions_home)
    gated_utils: dict[str, list[str]] = {}
    gated_verbs: dict[str, dict[str, list[str]]] = {}
    kind_sources: dict[str, list[str]] = {}
    for slug, req in lib.items():
        for kind in req.get("actions") or []:
            if kind in GATED_KINDS:
                kind_sources.setdefault(kind, []).append(slug)
        for util in req.get("utils") or []:
            # A BARE name reserves the whole util; `name:verb` reserves that one verb and
            # leaves the util's other verbs open (a mailbox is read behind its credential,
            # sent from behind the permission).
            name, verb = split_util_verb(util)
            if verb:
                gated_verbs.setdefault(name, {}).setdefault(verb, []).append(slug)
            else:
                gated_utils.setdefault(name, []).append(slug)
    caps, _ = normalize_capabilities(capabilities)
    return GrantPolicy(active=tuple(active or []),
                       # `<libraries_home>/permissions` by construction (ServerConfig), so the
                       # util catalog sits beside it — the reserved-util gate resolves a call's
                       # `calls:` tree from there, and write_util's create-vs-revise split
                       # asks it whether a name exists.
                       libraries_home=Path(permissions_home).parent,
                       actions=frozenset(k for k in caps.get("actions") or []
                                         if k in GATED_KINDS),
                       utils=frozenset(caps.get("utils") or []),
                       gated_utils={k: tuple(v) for k, v in gated_utils.items()},
                       gated_verbs={k: {v: tuple(d) for v, d in verbs.items()}
                                    for k, verbs in gated_verbs.items()
                                    if k not in gated_utils},
                       kind_sources={k: tuple(v) for k, v in kind_sources.items()},
                       confirm=caps.get("confirm") or "always",
                       rule_confirm=caps.get("rule_confirm") or "always",
                       remind_confirm=caps.get("remind_confirm") or "always",
                       # No baseline: unlike run history (D96's always-on 'last'), a reminder
                       # HOLDS a turn, so the layer stays off until the user switches it on.
                       reminders=caps.get("reminders") or "none",
                       # D96 (user decision 2026-08-20): own-runs read at 'last' depth is
                       # ALWAYS ON for a routine — baseline observability, like the state
                       # digest carrying the last result. The `runs: all` SETTING opens the
                       # whole archive (longitudinal work). Sub-workflow children DO load
                       # through here (empty caps), so the loop's depth>0 seam drops them
                       # back to "none" — a child's brief, not the archive, is its context.
                       run_history="all" if caps.get("runs") == "all" else "last",
                       denied=frozenset(k for k, v in (grants_map or {}).items()
                                        if v is False),
                       recipe_unlocked=recipe_unlocked,
                       admin=admin,
                       current_run_ts=current_run_ts)
