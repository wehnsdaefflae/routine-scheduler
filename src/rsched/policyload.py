"""BUILDING a `GrantPolicy` from config, and the path predicates it asks.

Split out of `grantpolicy.py` (F393): the policy OBJECT answers questions; this constructs it
and owns the two path questions that are policy rather than filesystem — is this the routine's
own recipe (so a write unlocks self-editing), and is this under `runs/` (engine-owned, read-only
to the run).
"""

from __future__ import annotations

from pathlib import Path

from .grantpolicy import GrantPolicy
from .grants import (
    _DEFAULT_RUNS_SOURCE,
    GATED_KINDS,
    _catalog_tags,
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
    kind_sources: dict[str, list[str]] = {}
    runs_sources: list[str] = []
    gated_tags: dict[str, list[str]] = {}
    for slug, req in lib.items():
        for kind in req.get("actions") or []:
            if kind in GATED_KINDS:
                kind_sources.setdefault(kind, []).append(slug)
        for util in req.get("utils") or []:
            # Key by the BARE name: a doc that reserves only `signal:read` still makes the
            # `signal` util gated, or the name lookup in deny() would miss it and the util
            # would be wide open — the fail-open direction.
            gated_utils.setdefault(split_util_verb(util)[0], []).append(slug)
        for tag in req.get("util_tags") or []:
            gated_tags.setdefault(tag, []).append(slug)
        if req.get("runs"):
            runs_sources.append(slug)
    # Expand tag classes into concrete gated utils against the LIVE catalog. Read it only when
    # a doc actually declares `util_tags` — with no tag gates in the library this costs nothing
    # and the policy is byte-identical to the name-only one.
    util_tag_index: dict[str, tuple[str, ...]] = {}
    if gated_tags:
        for util in _catalog_tags(permissions_home):
            tags = set(util["tags"])
            hit = tags & set(gated_tags)
            if not hit:
                continue
            util_tag_index[util["name"]] = tuple(sorted(tags))
            for tag in sorted(hit):
                for slug in gated_tags[tag]:
                    if slug not in gated_utils.setdefault(util["name"], []):
                        gated_utils[util["name"]].append(slug)
    caps, _ = normalize_capabilities(capabilities)
    # The create-vs-revise split needs to know which util names already exist — but only when
    # the routine holds exactly ONE half. Holding both makes every write_util allowed;
    # holding neither denies them all. Either way the catalog is not worth reading.
    held_write = {"write_util", "revise_util"} & set(caps.get("actions") or [])
    known_utils = (frozenset(u["name"] for u in _catalog_tags(permissions_home))
                   if len(held_write) == 1 else frozenset())
    return GrantPolicy(active=tuple(active or []),
                       known_utils=known_utils,
                       actions=frozenset(k for k in caps.get("actions") or []
                                         if k in GATED_KINDS),
                       utils=frozenset(caps.get("utils") or []),
                       util_tags=frozenset(caps.get("util_tags") or []),
                       util_tag_index=util_tag_index,
                       gated_utils={k: tuple(v) for k, v in gated_utils.items()},
                       kind_sources={k: tuple(v) for k, v in kind_sources.items()},
                       confirm=caps.get("confirm") or "always",
                       rule_confirm=caps.get("rule_confirm") or "always",
                       # D96 (user decision 2026-08-20): own-runs read at 'last' depth is
                       # ALWAYS ON for a routine — baseline observability, like the state
                       # digest carrying the last result. The run-history permission doc
                       # governs only the 'all' depth (longitudinal work stays an explicit
                       # opt-in). Sub-workflow children DO load through here (empty caps),
                       # so the loop's depth>0 seam drops them back to "none" — a child's
                       # brief, not the archive, is its context.
                       run_history="all" if caps.get("runs") == "all" else "last",
                       workflows=caps.get("workflows") or "catalog",
                       denied=frozenset(k for k, v in (grants_map or {}).items()
                                        if v is False),
                       recipe_unlocked=recipe_unlocked,
                       admin=admin,
                       runs_sources=tuple(runs_sources) or _DEFAULT_RUNS_SOURCE,
                       current_run_ts=current_run_ts)
