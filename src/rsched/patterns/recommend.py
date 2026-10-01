"""The RECOMMENDER — which pattern fits a routine and what THIS routine needs beyond it.

Two questions, asked at two moments. At creation: the new routine is saved with its pattern's
values; everything specific to it — the folders it reads, the mailbox its gate watches, the
finish line the person described — becomes PENDING changes the routine page shows under "check
the changes i recommend." On the page: "Recommend for this routine" asks the same second
question of a routine that already exists.

A recommendation is a proposal and never a write (`drafts.py`), so the person decides every
change with one "accept changes". It is also never trusted as it arrives: every proposed value
goes through the same validation the page's own save uses; one that does not validate is
dropped rather than offered — a proposal the accept button would refuse is not a proposal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import fields, store

log = logging.getLogger("rsched.patterns.recommend")

#: Fields a recommendation may not propose: a trigger's webhook is a token only the server
#: mints; the name and description are the routine's own.
_NOT_PROPOSED = ("triggers", "name", "description")

CHOICE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pattern", "why"],
    "properties": {"pattern": {"type": "string"}, "why": {"type": "string"}},
}

CHANGES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["changes"],
    "properties": {"changes": {
        "type": "array",
        "items": {"type": "object", "additionalProperties": False,
                  "required": ["field", "value_json", "reason"],
                  "properties": {
                      "field": {"type": "string"},
                      "value_json": {"type": "string",
                                     "description": "the field's COMPLETE new value as JSON"},
                      "reason": {"type": "string",
                                 "description": "one sentence the person reads beside it"}}},
    }},
}


def choose(server, *, workflow: str, name: str, task: str) -> str:
    """The pattern a routine on `workflow` follows: the one built on that workflow when there is
    exactly one, else the system model's pick among all of them. "" only when the library holds
    no pattern at all.
    """
    patterns = store.list_all(Path(server.libraries_home))
    if not patterns:
        return ""
    on_workflow = [p for p in patterns if p["workflow"] == workflow]
    if len(on_workflow) == 1:
        return on_workflow[0]["slug"]
    pool = on_workflow or patterns
    from ..workflows.suggest import _ask_json

    listing = "\n".join(f"- {p['slug']}: {p['summary']} WHEN: {p['when']}" for p in pool)
    obj, _why = _ask_json(server, (
        "Pick the settings pattern this recurring LLM-agent routine should follow. Answer with "
        "one slug from the list.\n\n"
        f"ROUTINE: {name}\nTASK:\n{task[:6000]}\n\nPATTERNS:\n{listing}"),
        CHOICE_SCHEMA, purpose=f"choose pattern → {name}")
    picked = str((obj or {}).get("pattern") or "")
    return picked if any(p["slug"] == picked for p in pool) else pool[0]["slug"]


def recommend(server, *, slug: str, saved: dict, pattern: dict | None,
              context: str) -> dict[str, dict]:
    """`{field: {value, reason}}` — what this routine needs that it does not hold now. Empty
    when the system model is unavailable or proposes nothing that validates.
    """
    from ..workflows.suggest import _ask_json

    allowed = [k for k in fields.GOVERNABLE if k not in _NOT_PROPOSED]
    prompt = _prompt(server, slug, saved, pattern, context, allowed)
    obj, why = _ask_json(server, prompt, CHANGES_SCHEMA, purpose=f"recommend settings → {slug}")
    if obj is None:
        log.warning("recommend(%s): no recommendation (%s)", slug, why)
        return {}
    out: dict[str, dict] = {}
    for row in obj.get("changes") or []:
        key = str(row.get("field") or "")
        if key not in allowed:
            continue
        try:
            value = json.loads(str(row.get("value_json") or "null"))
        except ValueError:
            continue
        if fields.equal(key, saved.get(key), value) or not valid(key, value, server):
            continue
        out[key] = {"value": fields.canonical(key, value),
                    "reason": str(row.get("reason") or "").strip()[:400]}
    return out


def valid(key: str, value: object, server: object | None = None) -> bool:
    """Would the page's own accept take this value? Checked with the validators it uses.

    The patch model checks a field's SHAPE; three fields are then checked for MEANING by the
    accept route itself (`web/config_fields.py`, called from `api_routine_patch.apply_updates`), and
    a value that clears the shape and fails the meaning is the worst kind to offer: the accept
    is whole-draft, so one refused field 400s every other change the person kept. That is how
    `connections: {"fau-mail": {"scopes": [...]}}` — a util's name where an OAuth provider
    belongs — cost ewsan-bohrdaten-steward all nine of its proposed settings. So run those
    validators here too. `models` and `machines` need the instance catalogs; without a server
    their semantic half is skipped rather than guessed.
    """
    from pydantic import ValidationError

    if key == "finish_line":
        from ..engine import finishline

        raw = value if isinstance(value, dict) else {}
        doc = finishline.normalize(raw)
        return not finishline.problems(doc) and all(
            isinstance(o, dict) and o.get("judge") in finishline.JUDGES
            for o in raw.get("outcomes") or [])
    from ..web.api_routine_patch import RoutinePatch

    pkey, pvalue = fields.patch_shape(key, value, lane_managed=False)
    try:
        RoutinePatch.model_validate({pkey: pvalue})
    except (ValidationError, ValueError, TypeError):
        return False
    return not _semantic_problem(key, value, server)


def _semantic_problem(key: str, value: object, server: object | None) -> bool:
    """True when the accept route's own validator would refuse this shaped-correct value."""
    from fastapi import HTTPException

    from ..web import config_fields

    try:
        if key == "connections":
            config_fields.validate_connections(value if isinstance(value, dict) else {})
        elif key == "machines" and server is not None:
            config_fields.validate_machines(server, value if isinstance(value, list) else [])
        elif key == "models" and server is not None:
            config_fields.validate_models(server, value if isinstance(value, dict) else {})
    except HTTPException as exc:
        log.info("recommend: dropping %s — the accept would refuse it (%s)", key, exc.detail)
        return True
    except Exception:  # a validator that cannot run is not evidence the value is bad
        log.warning("recommend: could not semantically check %s", key, exc_info=True)
    return False


def _prompt(server, slug: str, saved: dict, pattern: dict | None, context: str,
            allowed: list[str]) -> str:
    from .. import gatekit, library_docs
    from ..oauth.providers import PROVIDERS

    providers = ", ".join(sorted(PROVIDERS)) or "none configured"
    perms = "\n".join(f"- {d['slug']}: {d['summary']}"
                      for d in library_docs.list_docs(server.permissions_home))
    rules = "\n".join(f"- {d['slug']}: {d['summary']}"
                      for d in library_docs.list_docs(server.rules_home))
    # Each parameter with its type and meaning, as the console's gate form shows them: a name
    # alone left the model to guess formats — and the one weekday numbering this prompt spells
    # is the SCHEDULE's (0 = Sunday), while a `weekdays` check counts from Monday.
    kinds = "\n".join(
        f"- {kind}: {meaning}; params: " + ("; ".join(
            f"{name} ({typ}{', required' if required else ''}): {help_}"
            for name, (typ, required, help_) in spec.items()) or "none")
        for kind, (meaning, spec) in gatekit.KINDS.items())
    base = (f"It follows the pattern {pattern['slug']!r} ({pattern['summary']})."
            if pattern else "It follows no pattern yet.")
    return (
        "You configure a recurring LLM-agent routine. Propose ONLY the settings this routine "
        "needs that its current values lack — the things specific to it: the folders it "
        "reads or writes, the machine or account it uses, its cadence, a run gate that skips a "
        "scheduled fire ONLY when there is certainly nothing to do (a check that cannot tell "
        "counts as work; every gate should carry a max_quiet backstop), a finish line when "
        "its job ends, extra permissions or rules its work needs. Propose nothing the task does "
        "not call for, never widen access 'just in case', and leave a value alone when you are "
        "unsure. Each change carries the field's COMPLETE new value as JSON.\n\n"
        f"ROUTINE {slug}. {base}\n\nWHAT IT IS FOR:\n{context[:12000]}\n\n"
        f"ITS CURRENT SETTINGS (JSON):\n{json.dumps(saved, ensure_ascii=False)[:12000]}\n\n"
        f"FIELDS YOU MAY CHANGE: {', '.join(allowed)}.\n"
        'finish_line is {"outcomes": [{"text", "judge": "run"|"you"|"date", '
        '"date": "YYYY-MM-DD" (date only)}], "until": "YYYY-MM-DD" or ""}. '
        'run_gate is {"enabled", "timeout_s", "checks": [{"id", "kind", …params}]}. '
        'schedule is {"friendly": {"frequency": "hourly"|"daily"|"weekly"|'
        '"monthly"|"manual", "time": "HH:MM", "weekdays": [0-6, 0=Sunday]}, '
        '"catchup": "skip"|"run_once"}.\n'
        f'connections is {{provider: "account-label"}} and names an OAUTH provider only — '
        f'one of {providers}. A mailbox or an API the routine reaches through a UTIL is not a '
        'connection: that access is a permission or a secret, never a connections entry.\n\n'
        f"GATE CHECK KINDS:\n{kinds}\n\nPERMISSIONS:\n{perms}\n\nGENERAL RULES:\n{rules}")


def at_creation(server, *, slug: str, pattern_slug: str, context: str,
                finish_line: dict | None) -> list[str]:
    """After a routine is scaffolded on its pattern: write its pending changes — the finish
    line the person described, the pattern's `ask_first` values (which creation never writes),
    plus whatever the recommender finds — under the message the routine page leads with.
    Returns the fields proposed.
    """
    from ..config import load_routine
    from . import drafts

    cfg, _problems = load_routine(Path(server.routines_home) / slug)
    if cfg is None:
        return []
    saved = fields.snapshot(cfg)
    pattern = store.read(Path(server.libraries_home), pattern_slug) if pattern_slug else None
    changes = recommend(server, slug=slug, saved=saved, pattern=pattern, context=context)
    if pattern is not None:
        reason = f"the value {pattern['title']} carries — it decides access, so it waits for you"
        changes.update({key: {"value": value, "reason": reason}
                        for key, value in pattern["settings"].items()
                        if fields.BY_KEY[key].ask_first and key not in changes
                        and not fields.equal(key, saved.get(key), value)
                        and valid(key, value, server)})
    if finish_line and (finish_line.get("outcomes") or finish_line.get("until")) \
            and valid("finish_line", finish_line):
        changes["finish_line"] = {"value": fields.canonical("finish_line", finish_line),
                                  "reason": "the finish line you described while designing it"}
    if changes:
        drafts.write(Path(server.routines_home), slug, changes=changes, pattern=None,
                     message=CHECK_MESSAGE, source="creation")
    return sorted(changes)


CHECK_MESSAGE = "check the changes i recommend."
