"""DOMAIN-level config a member inherits (D82).

Split out of `config/routine.py` (F393): loading one routine's own config and merging a
domain's shared block over it are different jobs.

The merge is PRE-VALIDATION on purpose — a member's effective config is what validation sees, so
a domain cannot hand a member something the member alone would have been refused. List-valued
keys UNION (the domain is a floor a routine adds to); everything else merges per key with the
member's own value winning.

The layer this file merges is the DOMAIN's, never a lane's: a lane decides when routines fire
and owns no config at all, which is why deleting one returns its members to their own crons and
changes nothing else about them (docs/lanes-domains.md).
"""

from __future__ import annotations

from pathlib import Path

# The capability DIALS — single-valued, so a member's own copy SHADOWS the domain's (its key
# wins) rather than unioning with it the way the list members do. The approval dials among them
# are user policy, never a domain's to impose: `strip_shared_dials` drops a member's copy of one
# the domain already decides, so the domain's value is what a later change actually reaches.
CAPABILITY_DIALS = ("confirm", "rule_confirm", "remind_confirm", "runs", "workflows",
                    "reminders")


def apply_shared_config(raw: dict, shared_config: dict,
                        *, source: str = "the domain") -> tuple[dict, dict[str, str]]:
    """Merge ONE shared config layer into a member's raw routine.yaml (D82), returning
    (merged, provenance) where provenance maps each key the layer contributed to a short note
    for the UI ("permissions" → "3 from the domain", "grants" → "2 from the domain").

    `source` NAMES the layer in the provenance note. Only the domain uses this merge live
    today; template ADOPTION reuses the same union/fill rules to copy values in once
    (rsched/templates.py), where the note describes a write rather than an inheritance.

    The domain is a DEFAULT, never an override:

    - LIST keys (permissions, rules, machines, tags, fs roots) UNION — the domain is a floor a
      member adds to, member order first so its own choices read first.
    - MAPPING keys (models, connections, grants, budgets) merge PER KEY, the member's value
      winning. A member overrides one budget without losing the rest.
    - `capabilities` is both at once: its list members (actions/utils/util_tags) union, its
      dials (the three approval levels, runs, workflows, reminders) take the member's value
      when it sets one.

    Merging against the RAW mapping — before validation — is what makes "the member set it"
    mean *the key is in its file*, not *the model has a default*. Every field here has a
    non-empty default (budgets especially), so a merge over the validated model could never
    tell the two apart and the domain's value would be silently shadowed forever.
    """
    from ..domains import CONFIG_KEYS, CONFIG_LIST_KEYS

    merged = dict(raw)
    provenance: dict[str, str] = {}
    for key in CONFIG_KEYS:
        shared = shared_config.get(key)
        if not shared:
            continue
        own = raw.get(key)
        if key in CONFIG_LIST_KEYS:
            own_list = list(own) if isinstance(own, list) else []
            added = [v for v in shared if v not in own_list]
            if not added:
                continue
            merged[key] = own_list + added
            provenance[key] = f"{len(added)} from {source}"
        elif isinstance(shared, dict):
            own_map = dict(own) if isinstance(own, dict) else {}
            if key == "capabilities":
                merged[key], count = _merge_capabilities(own_map, shared)
            else:
                added_keys = [k for k in shared if k not in own_map]
                merged[key] = {**shared, **own_map}
                count = len(added_keys)
            if count:
                provenance[key] = f"{count} from {source}"
    return merged, provenance


def strip_shared_dials(caps: dict, shared_caps: dict, submitted: dict) -> dict:
    """Drop a member's own capability DIAL that its domain already decides — the inverse of
    `apply_shared_config`, applied on the save path.

    The raise/floor pair cannot express "unset": it emits a concrete value for EVERY dial, so
    a saved mapping otherwise records `runs: none` the member never chose. That copy then
    shadows the domain forever, because a member's own key always wins, so no later domain
    change could reach the routine. `submitted` is what the client actually sent, which is the
    only way to tell "the user turned this off" from "the user never touched it".

    A dial is dropped when the domain supplies it AND the client either omitted it or sent
    exactly the domain's value. Only dials: the list members (actions/utils/util_tags) UNION
    with the domain's, so a redundant entry there cannot shadow anything — and keeping it
    preserves what the user actually ticked.
    """
    return {k: v for k, v in caps.items()
            if not (k in CAPABILITY_DIALS and k in shared_caps
                    and (k not in submitted or submitted[k] == shared_caps[k]))}


def _merge_capabilities(own: dict, shared: dict) -> tuple[dict, int]:
    """capabilities: union the list members, member wins on the dials. Returns (merged, n)
    where n counts what the domain actually contributed — a dial the member already set is
    not inherited and must not be reported as such.
    """
    out = dict(own)
    count = 0
    for key, val in shared.items():
        if isinstance(val, list):
            own_list = list(own.get(key) or [])
            added = [v for v in val if v not in own_list]
            if added:
                out[key] = own_list + added
                count += len(added)
        elif key not in own:
            out[key] = val
            count += 1
    return out, count


def domain_config_for(routine_dir: Path, domain_id: str) -> tuple[dict, str]:
    """The shared config the named domain contributes, plus its name. Empty for no domain.

    Addressed BY ID out of the routine's own `domain:` key rather than found by scanning a
    membership list — which is what makes at-most-one a fact of the file. Its predecessor did
    scan one, combining whatever it found with "first record's value wins the whole key" while
    unioning WITHIN one, so what a routine inherited depended on the order rows happened to sit
    in a JSON file — an order no caller could have named. There is nothing to merge here, so
    there is no order to depend on.

    Domains live under `<routines_home>/.control/` and a routine dir sits directly in that
    home — so the home is the parent. The engine resolves the shared STORE from that same
    parent (engine/runtime.py), so the config a run inherits and the root it is handed can
    never be read out of two different homes: a run whose dir sits outside the routines home
    inherits nothing AND is handed nothing. A conversation or background task passes through
    here too; their homes hold no domains.json, so the lookup is empty either way.
    """
    from .. import domains

    if not domain_id:
        return {}, ""
    rec = domains.get(Path(routine_dir).parent, str(domain_id))
    if rec is None:
        # A routine naming a domain that has been deleted inherits nothing, which is the same
        # thing it inherited before it joined one. Reported by `rsched validate`
        # (cli._dangling_domains), never raised: a stale reference must not be the thing that
        # stops a run from booting; silence is what that costs — hence the report.
        return {}, ""
    return dict(rec.get("config") or {}), str(rec.get("name") or "")
