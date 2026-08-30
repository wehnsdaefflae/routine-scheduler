"""LLM workflow matcher: rank library workflows against a refined instruction."""

from __future__ import annotations

import json

from ..config import DEFAULT_DELIBERATION, DELIBERATION_LEVELS, ServerConfig
from ..endpoints import EndpointRegistry
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
        f"INSTRUCTION:\n{instruction}\n\nLIBRARY:\n{listing}\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(SUGGEST_SCHEMA)
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    obj = None
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model,
                                           schema=SUGGEST_SCHEMA, temperature=ref.temperature,
                                           effort=ref.effort, max_tokens=ref.max_tokens,
                                           timeout=120, purpose="Rank library workflows",
                                           kind="suggest")
        except Exception:
            # same graceful discipline as the sibling suggesters: creation flows degrade
            # to manual picking, they never 500 the creation flow
            return {"suggestions": [], "none_fit": True,
                    "new_workflow_hint": "suggester unavailable; pick manually"}
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, SUGGEST_SCHEMA)
            break
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
    if obj is None:
        return {"suggestions": [], "none_fit": True,
                "new_workflow_hint": "suggester reply was malformed; pick manually"}
    known = {w["slug"] for w in candidates}
    obj["suggestions"] = [s for s in obj.get("suggestions", []) if s["slug"] in known]
    obj["suggestions"].sort(key=lambda s: -s["confidence"])
    return obj


# --- tag vocabulary: every element carries >=3 tags; a new routine reuses the vocabulary --------


def existing_tags(server: ServerConfig) -> list[str]:
    """Union of tags already in use across every element — the vocabulary a new routine reuses."""
    import yaml

    from .. import library_docs, utils_lib
    tags: set[str] = set()
    for w in list_workflows(server.libraries_home):
        tags.update(w.get("tags") or [])
    for home in (server.rules_home, server.permissions_home):
        for d in library_docs.list_docs(home):
            tags.update(d.get("tags") or [])
    for u in utils_lib.list_utils(server.libraries_home):
        tags.update(u.get("tags") or [])
    for y in sorted(server.routines_home.glob("*/routine.yaml")):
        try:
            tags.update((yaml.safe_load(y.read_text(encoding="utf-8")) or {}).get("tags") or [])
        except Exception:
            pass
    return sorted(t for t in tags if isinstance(t, str) and t)


def normalize_tags(raw: list) -> list[str]:
    """Lowercase kebab-case, de-duplicated, at most 3 — the on-write shape for suggested tags."""
    import re as _re
    seen: set[str] = set()
    out: list[str] = []
    for t in raw or []:
        t = _re.sub(r"[^a-z0-9]+", "-", str(t).strip().lower()).strip("-")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:3]


RULES_PERMS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["rules", "permissions", "deliberation"],
    "properties": {"rules": {"type": "array", "items": {"type": "string"}},
                   "permissions": {"type": "array", "items": {"type": "string"}},
                   "deliberation": {"type": "string", "enum": list(DELIBERATION_LEVELS)}},
}


def suggest_rules_permissions(server: ServerConfig, instruction: str,
                              workflow_slug: str = "") -> dict:
    """Preselect the general RULES that will bind a new routine, its permissions
    (engine-enforced capabilities), and its deliberation level, from its
    instruction + chosen workflow. Returns {'rules': [...], 'permissions': [...],
    'deliberation': <level>}, validated against the library; falls back to the defaults
    when no endpoint answers. The creation flow shows the result as an editable preselection —
    this is a first pass, not a decision.
    """
    from .. import library_docs
    from ..config import DEFAULT_PERMISSIONS, DEFAULT_RULES

    rules = library_docs.list_docs(server.rules_home)
    perms = library_docs.list_docs(server.permissions_home)
    fallback = {"rules": [r for r in DEFAULT_RULES if r in {d["slug"] for d in rules}],
                "permissions": [p for p in DEFAULT_PERMISSIONS if p in {d["slug"] for d in perms}],
                "deliberation": DEFAULT_DELIBERATION}
    if not rules and not perms:
        return fallback
    workflow_note = ""
    if workflow_slug:
        wf = next((w for w in list_workflows(server.libraries_home)
                   if w["slug"] == workflow_slug), None)
        if wf:
            workflow_note = (f"\nCHOSEN WORKFLOW: {wf['slug']} — {wf['description']}\n"
                             f"Its suggested rules: {wf.get('includes') or '(none)'}")
    r_list = "\n".join(f"- {d['slug']}: {d['summary']}" for d in rules)
    p_list = "\n".join(f"- {d['slug']}: {d['summary']}"
                       + (f" [requires: {d['requires']}]" if d.get("requires") else "")
                       for d in perms)
    prompt = (
        "A new recurring LLM-agent routine is being created. Pick the general RULES that will "
        "bind it (shared library prose the run applies to its own case) and its PERMISSIONS "
        "(conduct docs whose required capabilities the engine then enforces) from the catalogs "
        "below.\n\n"
        f"INSTRUCTION:\n{instruction}\n{workflow_note}\n\n"
        f"RULES:\n{r_list}\n\nPERMISSIONS:\n{p_list}\n\n"
        "Guidance: include ask-policy and decision-record for almost everything, and "
        "intent-inference wherever the user will correct the routine's output. "
        "From the rest take only what the task actually exercises: "
        "evidence-discipline whenever the routine REPORTS findings someone acts on; "
        "change-restraint and error-recovery for routines that edit code or drive tools; "
        "independent-verification when the deliverable is irreversible, outward-facing, or "
        "expensive to redo; review-recall for review/audit/scan tasks; decision-commitment for "
        "open-ended work on a tight turn budget; teaching-insights only when a human reads the "
        "output as it is produced; interface-design when the routine BUILDS or restyles UI; "
        "interface-copy when it writes text a person reads as a product surface (UI labels, "
        "notifications, report headings); test-design and failure-visibility when the routine "
        "WRITES code that others will run. Each rule is one more thing the run must read and "
        "honour — do not take the whole set by default.\n"
        "Pick permissions conservatively: only what the task clearly needs (e.g. a "
        "messaging-* channel only if the task must reach a person outside the web UI; "
        "run-history only if runs build on each other's details beyond the last summary; "
        "shell almost never).\n\n"
        "Also pick DELIBERATION — how much of the model's thinking should land on paper "
        "as it works: 'terse' for purely mechanical pipelines (fetch, convert, file); "
        "'standard' for ordinary tasks; 'deliberate' when steps involve judgment that "
        "benefits from world knowledge beyond the immediate inputs (evaluating, ranking, "
        "curating, writing for a reader); 'think-on-paper' only for genuinely "
        "decision-heavy analysis where reasoning must persist across a long run.\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(RULES_PERMS_SCHEMA)
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model,
                                           schema=RULES_PERMS_SCHEMA, temperature=ref.temperature,
                                           effort=ref.effort, max_tokens=ref.max_tokens,
                                           timeout=120,
                                           purpose="Suggest rules & permissions", kind="suggest")
        except Exception:
            return fallback
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, RULES_PERMS_SCHEMA)
            known_r = {d["slug"] for d in rules}
            known_p = {d["slug"] for d in perms}
            level = obj.get("deliberation")
            return {"rules": [r for r in obj.get("rules", []) if r in known_r],
                    "permissions": [p for p in obj.get("permissions", []) if p in known_p],
                    "deliberation": level if level in DELIBERATION_LEVELS
                                    else DEFAULT_DELIBERATION}
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
    return fallback


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
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model, schema=RECOMMEND_SCHEMA,
                                           temperature=ref.temperature, effort=ref.effort,
                                           max_tokens=ref.max_tokens, timeout=120,
                                           purpose="Recommend routine setup", kind="suggest")
        except Exception:
            return fallback
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, RECOMMEND_SCHEMA)
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
            continue
        recommend_of: dict[str, bool] = {}
        reason_of: dict[str, str] = {}
        for it in obj.get("items", []):
            slug = it.get("slug")
            if slug in kind_of:                       # drop hallucinated slugs
                recommend_of[slug] = bool(it.get("recommend"))
                reason_of[slug] = (it.get("reason") or "").strip()
        return _rows(recommend_of, reason_of, available=True)
    return fallback

