"""The suggesters: everything the console asks the SYSTEM MODEL on a person's behalf.

Four of them, all the same shape — build a prompt, get one JSON object back, degrade quietly if
it does not come: rank the library's workflows against an instruction, propose the rules and
permissions a new routine should hold, recommend setup for an existing one, and write a routine's
description at create time. None of them may fail the flow they sit in; a person is waiting on a
form, and "pick it yourself" is always an acceptable answer where "500" is not.
"""

from __future__ import annotations

import json

from ..config import ServerConfig
from ..endpoints import EndpointRegistry
from ..paths import read_yaml
from ..schema_guard import SchemaViolation, parse_reply
from .library import list_workflows

SUGGEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suggestions", "none_fit"],
    "properties": {
        "suggestions": {
            "type": "array", "maxItems": 3,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["slug", "confidence", "reason"],
                      "properties": {"slug": {"type": "string"},
                                     "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                     "reason": {"type": "string"}}},
        },
        "none_fit": {"type": "boolean",
                     "description": "true when no listed workflow fits well and a new one "
                                    "should be drafted"},
        "new_workflow_hint": {"type": "string",
                              "description": "when none_fit: one paragraph sketching the "
                                             "missing workflow"},
    },
}


def _ask_json(server: ServerConfig, prompt: str, schema: dict, *,
              purpose: str) -> tuple[dict | None, str]:
    """One schema-valid JSON object from the system model: `(obj, "")`, or `(None, why)`.

    Every suggester in this module wants exactly this and nothing more: ask once, and if the
    reply does not satisfy the schema, show the model its own reply plus the violation and ask
    again. One retry, because a model that cannot produce the shape twice will not produce it on
    a third try either, and these calls sit in front of a person waiting on a form.

    `why` is `"unavailable"` (no system model, or the endpoint failed) or `"malformed"` (it
    answered twice and neither answer fit the schema). Both mean "pick it yourself", so most
    callers drop it — but they are different facts about the instance, one a configuration
    problem and one a model problem, and `suggest()` puts which it was in front of the user.

    Written once because it was written four times, and the copies had already drifted: three of
    them called `for_system()` OUTSIDE the try, so an instance with no system_model configured
    got an EndpointError out of a function whose own comment promised it "never 500s the creation
    flow". The fourth copy had the guard, and nothing made the other three grow one.
    """
    try:
        endpoint, ref = EndpointRegistry(server).for_system()
    except Exception:
        return None, "unavailable"
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                           temperature=ref.temperature, effort=ref.effort,
                                           max_tokens=ref.max_tokens, timeout=120,
                                           purpose=purpose, kind="suggest")
        except Exception:
            return None, "unavailable"
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, schema)
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
        else:
            return obj, ""
    return None, "malformed"


def suggest(server: ServerConfig, instruction: str) -> dict:
    candidates = list(list_workflows(server.libraries_home))
    if not candidates:
        return {"suggestions": [], "none_fit": True,
                "new_workflow_hint": "library has no workflows yet"}
    listing = "\n\n".join(
        f"- slug: {w['slug']}\n  description: {w['description']}\n  when_to_use: {w['when_to_use']}"
        for w in candidates)
    prompt = (
        "An instruction for a recurring LLM agent routine needs a control-flow workflow from "
        "the library below. Rank up to 3 fitting workflows with confidence 0-1 and a one-line "
        "reason each; set none_fit=true (with new_workflow_hint) if nothing fits well.\n\n"
        # R1165/R1181: two patterns can describe the same SUBJECT ("coach an application",
        # "steward a project") and differ only in the machinery a routine is born with — where
        # its state lives, what it publishes to, which services it assumes. Ranked on subject
        # words alone the matcher picks the one whose prose is most colourful, and the routine
        # is born wired to a store or a host it will never reach. So the deciding dimension is
        # stated, and stated first.
        "Rank on MECHANISM before subject. What decides the fit is the shape of the work — how "
        "the routine's state persists between runs, what it publishes or delivers, what it "
        "reads each run, and whether a human edits its output in between — not how many of the "
        "instruction's topic words a workflow's prose happens to repeat. Two workflows that "
        "describe the same subject with different machinery are NOT close: picking the wrong "
        "one gives the routine a persistence or publishing model it cannot reach. Where a "
        "workflow states which mechanism it is for, treat that as binding.\n\n"
        f"INSTRUCTION:\n{instruction}\n\nLIBRARY:\n{listing}\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(SUGGEST_SCHEMA)
    )
    obj, why = _ask_json(server, prompt, SUGGEST_SCHEMA, purpose="Rank library workflows")
    if obj is None:
        hint = ("suggester reply was malformed; pick manually" if why == "malformed"
                else "suggester unavailable; pick manually")
        return {"suggestions": [], "none_fit": True, "new_workflow_hint": hint}
    known = {w["slug"] for w in candidates}
    obj["suggestions"] = [s for s in obj.get("suggestions", []) if s["slug"] in known]
    obj["suggestions"].sort(key=lambda s: -s["confidence"])
    return obj


# ---------------------------------------------------- recommend_setup() ------------------------
# The INVERSE of the setup surface. readmodels/surface.py reads FORWARD from what a routine holds
# ("what does this still need?"); this reads from what the routine DOES and judges, for every rule
# and permission in the catalogs, whether THIS routine should hold it — with a one-line why/why-not
# a person can act on. It powers the routine page's "Recommend" button: advice beside every toggle,
# never an automatic change (the user stays the one who flips the switch).

RECOMMEND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["slug", "recommend", "reason"],
            "properties": {"slug": {"type": "string"},
                           "recommend": {"type": "boolean"},
                           "reason": {"type": "string"}}}},
    },
}


def _recipe_text(cfg) -> str:
    """The routine's task as prose for the recommender: its one-line description plus its
    recipe entry (main.md). Bounded so a long recipe never blows the prompt.
    """
    parts: list[str] = []
    if cfg.description:
        parts.append(str(cfg.description))
    try:
        main = cfg.dir / "main.md"
        if main.is_file():
            parts.append(main.read_text(encoding="utf-8"))
    except OSError:
        pass
    return "\n\n".join(parts)[:12000]


def recommend_setup(server: ServerConfig, cfg) -> dict:
    """Recommend, for an EXISTING routine, which general RULES and PERMISSIONS it should hold,
    each with a one-line reason judged against its recipe. Returns
    ``{'available': bool, 'items': [{slug, kind, held, recommend, reason}, ...]}`` — one row per
    catalog item, `held` its current state, `recommend` whether the task needs it. Degrades to
    ``available=False`` (rows carry their held state, no advice) when no endpoint answers, so the
    page shows the toggles without advice rather than 500ing — the same discipline as the sibling
    suggesters.
    """
    from .. import library_docs

    rules = library_docs.list_docs(server.rules_home)
    perms = library_docs.list_docs(server.permissions_home)
    held_rules = set(cfg.rules or [])
    held_perms = set(cfg.permissions or [])
    kind_of = {d["slug"]: "rule" for d in rules}
    kind_of.update({d["slug"]: "permission" for d in perms})
    held_of = {d["slug"]: (d["slug"] in held_rules) for d in rules}
    held_of.update({d["slug"]: (d["slug"] in held_perms) for d in perms})

    def _rows(recommend_of: dict[str, bool], reason_of: dict[str, str], available: bool) -> dict:
        return {"available": available,
                "items": [{"slug": s, "kind": kind_of[s], "held": held_of[s],
                           "recommend": recommend_of.get(s, held_of[s]),
                           "reason": reason_of.get(s, "")} for s in kind_of]}

    if not rules and not perms:
        return {"available": False, "items": []}
    # graceful default: every row keeps its current state, no advice
    fallback = _rows({}, {}, available=False)

    def _fmt(docs: list[dict], held: set[str]) -> str:
        out = []
        for d in docs:
            when = (d.get("effect") or {}).get("when")
            line = f"- {d['slug']}: {d['summary']}"
            if when:
                line += f" | hold it when: {when}"
            if d["slug"] in held:
                line += " | CURRENTLY HELD"
            out.append(line)
        return "\n".join(out)

    prompt = (
        "An EXISTING recurring LLM-agent routine holds a set of general RULES (shared library "
        "principle prose the run applies to its own case) and PERMISSIONS (conduct docs whose "
        "capabilities the engine then enforces). For EVERY item in both catalogs below, judge "
        "whether THIS routine should hold it: set recommend=true when its task genuinely "
        "exercises the item, false when it does not — and give a ONE-LINE reason grounded in what "
        "this routine actually does. Use each item's 'hold it when' clause as the test. Be "
        "conservative with permissions (a messaging-* channel only if the routine must reach a "
        "person outside the web UI; run-history only if runs build on each other beyond the last "
        "summary; shell almost never) and take a rule only where the task exercises it — every "
        "held rule is one more thing each run must read and honour.\n\n"
        f"ROUTINE: {cfg.name or cfg.slug}\nWHAT IT DOES:\n{_recipe_text(cfg)}\n\n"
        f"RULES:\n{_fmt(rules, held_rules)}\n\nPERMISSIONS:\n{_fmt(perms, held_perms)}\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(RECOMMEND_SCHEMA)
    )
    obj, _why = _ask_json(server, prompt, RECOMMEND_SCHEMA, purpose="Recommend routine setup")
    if obj is None:
        return fallback
    recommend_of: dict[str, bool] = {}
    reason_of: dict[str, str] = {}
    for it in obj.get("items", []):
        slug = it.get("slug")
        if slug in kind_of:                           # drop hallucinated slugs
            recommend_of[slug] = bool(it.get("recommend"))
            reason_of[slug] = (it.get("reason") or "").strip()
    return _rows(recommend_of, reason_of, available=True)


# ---------------------------------------------------- generate_description() -------------------
# Routine descriptions used to be the routine's NAME (scaffold wrote `description = name`). This
# generates a COMPREHENSIVE one — purpose, requirements, side effects, and dependencies with other
# routines — at create time, from the routine's own task plus the catalog of siblings that already
# exist (so a named dependency is a real routine, not an invention). Same graceful discipline as
# the sibling suggesters: it never fails the creation flow, it falls back to the name.

DESCRIBE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["description"],
    "properties": {"description": {"type": "string"}},
}


def _sibling_catalog(server: ServerConfig) -> str:
    """Compact list of the OTHER routines that already exist — slug · name · tags — so a
    generated description can name real inter-routine dependencies instead of inventing them.
    Bounded (60 rows) so a large instance never blows the prompt; best-effort per file.
    """
    import yaml

    lines: list[str] = []
    for y in sorted(server.routines_home.glob("*/routine.yaml")):
        try:
            cfg = read_yaml(y, {})
        except (OSError, yaml.YAMLError):
            continue
        nm = str(cfg.get("name") or y.parent.name)
        tags = ", ".join(t for t in (cfg.get("tags") or []) if isinstance(t, str))
        lines.append(f"- {y.parent.name}: {nm}" + (f" [{tags}]" if tags else ""))
        if len(lines) >= 60:
            break
    return "\n".join(lines)


def generate_description(server: ServerConfig, *, name: str, instruction: str,
                         workflow_slug: str = "", recipe_text: str = "") -> str:
    """A COMPREHENSIVE routine description generated from its task: what one run PRODUCES and why
    (purpose), what it REQUIRES (permissions / secrets / inputs / external services), its SIDE
    EFFECTS (what it writes, publishes or sends outside itself), and its DEPENDENCIES with other
    routines (which it feeds, consumes from, or shares a domain and its store with). Replaces
    the old `description = name`. Returns a dense multi-sentence string; falls back to `name`
    whenever the task is empty, no endpoint answers, or the reply is blank — the creation flow
    never fails on this. `recipe_text` (an existing routine's main.md) is used in place of
    `instruction` when regenerating a description for a routine that already exists.
    """
    task = (recipe_text or instruction or "").strip()
    if not task:
        return name
    wf_note = ""
    if workflow_slug:
        wf = next((w for w in list_workflows(server.libraries_home)
                   if w["slug"] == workflow_slug), None)
        if wf:
            wf_note = f"\nWORKFLOW PATTERN: {wf['slug']} — {wf['description']}"
    siblings = _sibling_catalog(server)
    sib_note = ("\n\nOTHER ROUTINES THAT ALREADY EXIST (name dependencies ONLY from these — "
                f"omit the dependencies sentence if none apply):\n{siblings}" if siblings else "")
    prompt = (
        "Write a COMPREHENSIVE description of the recurring LLM-agent routine below, for the "
        "person who manages it. Do NOT restate the name; describe what the routine actually does, "
        "as flowing prose (no headings, no bullet list). Cover, in this order and only where they "
        "apply:\n"
        "1. PURPOSE — what one run produces and why it matters.\n"
        "2. REQUIREMENTS — the permissions, secrets, inputs or external services it depends on.\n"
        "3. SIDE EFFECTS — what it writes, publishes, sends or changes OUTSIDE itself each run.\n"
        "4. DEPENDENCIES WITH OTHER ROUTINES — which existing routines it feeds, consumes from, "
        "or shares a domain and its shared store with.\n\n"
        f"ROUTINE NAME: {name}{wf_note}\n\nTASK:\n{task[:8000]}{sib_note}\n\n"
        "Keep it factual and specific — 3 to 6 sentences, at most ~700 characters. Reply with "
        "ONLY one JSON object matching this schema (no prose):\n" + json.dumps(DESCRIBE_SCHEMA)
    )
    obj, _why = _ask_json(server, prompt, DESCRIBE_SCHEMA, purpose="Generate routine description")
    if obj is None:
        return name                          # no system model, or no usable reply — keep the name
    return str(obj.get("description") or "").strip() or name

