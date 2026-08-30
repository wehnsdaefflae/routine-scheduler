"""GROUP-level config a member inherits (D82).

Split out of `config/routine.py` (F393): loading one routine's own config and merging a group's
shared block over it are different jobs.

The merge is PRE-VALIDATION on purpose — a member's effective config is what validation sees, so
a group cannot hand a member something the member alone would have been refused. List-valued
keys UNION (the group is a floor a routine adds to); everything else merges per key with the
member's own value winning.
"""

# approval dials are user policy, never a group's to impose — stripped from a group block.
from __future__ import annotations

from pathlib import Path

# The capability DIALS — single-valued, so a member's own copy SHADOWS the group's (its key
# wins) rather than unioning with it the way the list members do.
CAPABILITY_DIALS = ("confirm", "rule_confirm", "runs", "workflows")



def apply_group_config(raw: dict, group_config: dict,
                       *, source: str = "the group") -> tuple[dict, dict[str, str]]:
    """Merge ONE shared config layer into a member's raw routine.yaml (D82), returning
    (merged, provenance) where provenance maps each key the layer contributed to a short note
    for the UI ("permissions" → "3 from the group", "grants" → "2 from the group").

    `source` NAMES that layer in the provenance note, because two layers use this merge on the
    same terms — a group's shared block and a settings TEMPLATE (rsched/templates.py) — and the
    routine page has to tell an operator which one supplied a value. Composing the phrase at the
    call site instead produced "3 from the group from the template" for every templated routine.

    The group is a DEFAULT, never an override:

    - LIST keys (permissions, rules, machines, tags, fs roots) UNION — the group is a floor a
      member adds to, member order first so its own choices read first.
    - MAPPING keys (models, connections, grants, budgets) merge PER KEY, the member's value
      winning. A member overrides one budget without losing the rest.
    - `capabilities` is both at once: its list members (actions/utils/util_tags) union, its
      dials (confirm/rule_confirm/runs/workflows) take the member's value when it sets one.

    Merging against the RAW mapping — before validation — is what makes "the member set it"
    mean *the key is in its file*, not *the model has a default*. Every field here has a
    non-empty default (budgets especially), so a merge over the validated model could never
    tell the two apart and the group's value would be silently shadowed forever.
    """
    from ..groups import CONFIG_KEYS, CONFIG_LIST_KEYS

    merged = dict(raw)
    provenance: dict[str, str] = {}
    for key in CONFIG_KEYS:
        shared = group_config.get(key)
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

def strip_group_dials(caps: dict, group_caps: dict, submitted: dict) -> dict:
    """Drop a member's own capability DIAL that its group already decides — the inverse of
    `apply_group_config`, applied on the save path.

    The raise/floor pair cannot express "unset": it emits a concrete value for EVERY dial, so
    a saved mapping otherwise records `runs: none` the member never chose. That copy then
    shadows the group forever, because a member's own key always wins, and no later group
    change could reach the routine. `submitted` is what the client actually sent, which is the
    only way to tell "the user turned this off" from "the user never touched it".

    A dial is dropped when the group supplies it AND the client either omitted it or sent
    exactly the group's value. Only dials: the list members (actions/utils/util_tags) UNION
    with the group's, so a redundant entry there cannot shadow anything, and keeping it
    preserves what the user actually ticked.
    """
    return {k: v for k, v in caps.items()
            if not (k in CAPABILITY_DIALS and k in group_caps
                    and (k not in submitted or submitted[k] == group_caps[k]))}

def _merge_capabilities(own: dict, shared: dict) -> tuple[dict, int]:
    """capabilities: union the list members, member wins on the dials. Returns (merged, n)
    where n counts what the group actually contributed — a dial the member already set is
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

def group_config_for(routine_dir: Path, slug: str) -> tuple[dict, str]:
    """The shared config a routine inherits, plus the name of the group it came from.

    Groups live under `<routines_home>/.control/`, and a routine dir sits directly in that
    home — so the home is the parent. A conversation or background task passes through here
    too; their homes hold no groups.json, so the lookup is empty and nothing is inherited.
    Membership of several groups merges in listed order, first group's value winning.
    """
    from .. import groups

    home = Path(routine_dir).parent
    if not groups.groups_file(home).is_file():
        return {}, ""
    merged: dict = {}
    names: list[str] = []
    for g in groups.list_groups(home):
        if slug not in groups.member_slugs(g) or not g.get("config"):
            continue
        names.append(g.get("name") or g.get("id") or "")
        for key, val in g["config"].items():
            if key not in merged:
                merged[key] = val
    return merged, ", ".join(n for n in names if n)
