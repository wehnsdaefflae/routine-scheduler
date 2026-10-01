"""The settings VOCABULARY — every value on a routine page that a pattern may carry, in one
canonical, comparable form.

Three readers need the same answer to "is this routine's value the pattern's value?": the page
(which marks an override), the recommender (which proposes only real differences) and the
"Save as new pattern" button (enabled only when the routine matches no pattern at all). One
canonical form per field is what keeps them agreeing — a list of rules is a SET, so `[a, b]` and
`[b, a]` are the same value; a trigger's id and webhook token are identity, not configuration,
so two routines with a report trigger each hold the same value.

A field is `governable` when a pattern may carry it; `name` and `description` are never — they
are what makes one routine this routine. `ask_first` fields are never applied without the
person's click, even at creation: a pattern pre-deciding which shared secrets a routine may
receive would be a pattern granting secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config.base import DEFAULT_BUDGETS


@dataclass(frozen=True)
class Field:
    key: str
    group: str
    label: str
    shape: str          # "set" | "map" | "scalar" | "doc"
    governable: bool = True
    ask_first: bool = False


FIELDS: tuple[Field, ...] = (
    Field("schedule", "schedule", "Schedule", "doc"),
    Field("run_gate", "schedule", "Run gate", "doc"),
    Field("triggers", "schedule", "Triggers", "doc"),
    Field("improve", "schedule", "Include in improvement", "scalar"),
    Field("permissions", "abilities", "Permissions", "set"),
    Field("capabilities", "abilities", "Capabilities", "doc"),
    Field("rules", "abilities", "General rules", "set"),
    Field("reminders", "abilities", "Shared reminders", "set"),
    Field("grants", "access", "Secret exposure & declined access", "map", ask_first=True),
    Field("finish_line", "goal", "Finish line", "doc"),
    Field("budgets", "limits", "Budgets", "map"),
    Field("keep_runs", "limits", "Retention", "scalar"),
    Field("fs_read_roots", "reach", "Read-only roots", "set"),
    Field("fs_write_roots", "reach", "Read-write roots", "set"),
    Field("connections", "reach", "Connections", "map"),
    Field("machines", "reach", "Machines", "set"),
    Field("models", "models", "Models", "map"),
    Field("deliberation", "models", "Deliberation", "scalar"),
    Field("tags", "identity", "Tags", "set"),
    Field("name", "identity", "Name", "scalar", governable=False),
    Field("description", "identity", "Description", "scalar", governable=False),
    Field("hub_tab", "identity", "Hub tab", "scalar", governable=False),
)
BY_KEY = {f.key: f for f in FIELDS}
GOVERNABLE = tuple(f.key for f in FIELDS if f.governable)

#: A trigger's identity and its secret are not configuration: two routines each holding a report
#: trigger hold the same setting, whatever their ids and whatever their webhook tokens.
_TRIGGER_IDENTITY = ("id", "token", "created")


def canonical(key: str, value: object) -> object:
    """The comparable form of one field's value. Unknown keys raise — a field that is not in
    the vocabulary is a field no pattern can govern and no page can mark.
    """
    f = BY_KEY[key]
    if value is None:
        return {"set": [], "map": {}}.get(f.shape)
    if f.shape == "set":
        return sorted({str(v) for v in value}) if isinstance(value, (list, tuple, set)) else []
    if key == "capabilities":
        caps = value if isinstance(value, dict) else {}
        return {k: (sorted({str(x) for x in v}) if isinstance(v, (list, tuple, set)) else v)
                for k, v in sorted(caps.items()) if v not in (None, [], "")}
    if key == "triggers":
        from ..triggers import with_defaults

        # defaults spelled out: routine.yaml leaves them out, the validated config names them
        rows = [{k: v for k, v in sorted(with_defaults(t).items()) if k not in _TRIGGER_IDENTITY}
                for t in (value if isinstance(value, list) else []) if isinstance(t, dict)]
        return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
    if key == "finish_line":
        return _canonical_finish_line(value if isinstance(value, dict) else {})
    if f.shape in ("map", "doc"):
        return json.loads(json.dumps(value, sort_keys=True, default=str))
    return value


def _canonical_finish_line(doc: dict) -> dict:
    """What the operator set — never what the runs reported about it (distances, evidence)."""
    from ..engine.finishline import ENGINE_OWNED, assign_ids, normalize

    norm = normalize(doc)
    assign_ids(norm["outcomes"])
    return {"outcomes": [{k: v for k, v in sorted(o.items()) if k not in ENGINE_OWNED}
                         for o in norm["outcomes"]],
            "until": norm["until"]}


def equal(key: str, a: object, b: object) -> bool:
    return json.dumps(canonical(key, a), sort_keys=True) == json.dumps(canonical(key, b),
                                                                       sort_keys=True)


def diff(before: dict, after: dict, keys: list[str] | None = None) -> dict[str, dict]:
    """Field → `{before, after}` (plus `added`/`removed` for a set) for every key whose value
    differs. `keys` limits the comparison — a pattern compares only what it governs.
    """
    out: dict[str, dict] = {}
    for key in keys if keys is not None else [f.key for f in FIELDS]:
        a, b = before.get(key), after.get(key)
        if equal(key, a, b):
            continue
        row: dict = {"before": canonical(key, a), "after": canonical(key, b)}
        if BY_KEY[key].shape == "set":
            row["added"] = sorted(set(row["after"]) - set(row["before"]))
            row["removed"] = sorted(set(row["before"]) - set(row["after"]))
        out[key] = row
    return out


def overrides(settings: dict, pattern_settings: dict) -> list[str]:
    """The governed fields where this routine departs from its pattern."""
    return sorted(diff(settings, pattern_settings, [k for k in pattern_settings
                                                    if k in BY_KEY]))


def matches(settings: dict, pattern_settings: dict) -> bool:
    return not overrides(settings, pattern_settings)


def snapshot(cfg, *, deliberation: str | None = None) -> dict:
    """One routine's settings as a canonical document — what its page shows, what a pattern
    is compared against, and what "Save as new pattern" copies into the library.
    """
    from ..engine import finishline
    from ..schedule import cron_to_friendly

    friendly = cron_to_friendly(cfg.cron) if cfg.enabled else {"frequency": "disabled"}
    values = {
        "schedule": {"friendly": friendly, "catchup": cfg.catchup},
        "run_gate": cfg.run_gate.model_dump(),
        "triggers": list(cfg.triggers or []),
        "improve": bool(cfg.improve),
        "permissions": list(cfg.permissions or []),
        "capabilities": effective_capabilities(cfg.capabilities),
        "rules": list(cfg.rules or []),
        "reminders": list(cfg.shared_reminders or []),
        "grants": dict(cfg.grants or {}),
        "finish_line": finishline.load(Path(cfg.dir)),
        "budgets": {**DEFAULT_BUDGETS, **(cfg.budgets or {})},
        "keep_runs": int(cfg.keep_runs),
        "fs_read_roots": [tilde(p) for p in cfg.fs_read_roots],
        "fs_write_roots": [tilde(p) for p in cfg.fs_write_roots],
        "connections": dict(cfg.connections or {}),
        "machines": list(cfg.machines or []),
        "models": dict(cfg.models or {}),
        "deliberation": deliberation or cfg.deliberation,
        "tags": list(cfg.tags or []),
        "name": cfg.name,
        "description": cfg.description,
        "hub_tab": cfg.hub_tab,
    }
    return {k: canonical(k, v) for k, v in values.items()}


def effective_capabilities(caps: object) -> dict:
    """A capabilities mapping with every setting present — what the routine's file MEANS.

    A file that leaves a setting out holds its default, so comparing the raw mappings would
    call two routines with the same effective settings different and mark an override
    nobody chose.
    """
    from ..config.base import DEFAULT_CAPABILITIES
    from ..grants import EMPTY_CAPABILITIES

    raw = dict(caps) if isinstance(caps, dict) else {}
    out: dict = {"actions": list(raw.get("actions") or []),
                 "utils": list(raw.get("utils") or [])}
    for key in ("confirm", "rule_confirm", "remind_confirm", "runs", "reminders"):
        out[key] = raw.get(key) or DEFAULT_CAPABILITIES.get(key) or EMPTY_CAPABILITIES[key]
    return out


def patch_shape(key: str, value: object, lane_managed: bool) -> tuple[str, object]:
    """One settings value in the shape the validated routine PATCH takes."""
    if key == "schedule":
        spec = dict(value) if isinstance(value, dict) else {}
        friendly = spec.get("friendly") or {"frequency": "manual"}
        if friendly.get("frequency") == "disabled":
            return "schedule", {"disabled": True}
        if lane_managed:
            return "schedule", {"disabled": False}
        return "schedule", {"friendly": friendly, "catchup": spec.get("catchup") or "skip"}
    if key == "reminders":
        return "shared_reminders", value
    return key, value


def tilde(path: object) -> str:
    """$HOME → ~, so a root written into settings — a routine's at creation, a pattern saved
    from one — never embeds the account's home path. Only $HOME itself or a path under it:
    a sibling that merely shares its prefix (`/home/me-data` beside `/home/me`) is kept.
    """
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") else text
