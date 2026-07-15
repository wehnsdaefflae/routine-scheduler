"""LLM workflow matcher: rank library workflows against a refined instruction."""

from __future__ import annotations

import json

from ..config import ServerConfig
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

# Workflows tagged 'meta' are internal machinery (wizard, library maintenance, self-audit) —
# excluded from user-facing suggestion. This replaces the old hardcoded name set with the tag.
INTERNAL_TAG = "meta"


def suggest(server: ServerConfig, instruction: str) -> dict:
    candidates = [w for w in list_workflows(server.library_home)
                  if INTERNAL_TAG not in (w.get("tags") or [])]
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
        completion = endpoint.complete(messages, model=ref.model,
                                       schema=SUGGEST_SCHEMA, temperature=ref.temperature,
                                       timeout=120, purpose="Rank library workflows",
                                       kind="suggest")
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


# --- tag suggestion: every element carries >=3 tags; a new routine reuses the vocabulary --------

TAGS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tags"],
    "properties": {"tags": {"type": "array", "minItems": 3, "maxItems": 3,
                            "items": {"type": "string"}}},
}


def existing_tags(server: ServerConfig) -> list[str]:
    """Union of tags already in use across every element — the vocabulary a new routine reuses."""
    import yaml

    from .. import library_docs, utils_lib
    tags: set[str] = set()
    for w in list_workflows(server.library_home):
        tags.update(w.get("tags") or [])
    for home in (server.traits_home, server.permissions_home):
        for d in library_docs.list_docs(home):
            tags.update(d.get("tags") or [])
    for u in utils_lib.list_utils(server.utils_home):
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


TRAITS_PERMS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["traits", "permissions"],
    "properties": {"traits": {"type": "array", "items": {"type": "string"}},
                   "permissions": {"type": "array", "items": {"type": "string"}}},
}


def suggest_traits_permissions(server: ServerConfig, instruction: str,
                               workflow_slug: str = "") -> dict:
    """Preselect the traits (practice modules, adapted in at creation) and permissions
    (engine-enforced capabilities) for a new routine, from its instruction + chosen
    workflow. Returns {'traits': [...], 'permissions': [...]}, validated against the
    library; falls back to the defaults when no endpoint answers. The wizard shows the
    result as an editable preselection — this is a first pass, not a decision.
    """
    from .. import library_docs
    from ..config import DEFAULT_PERMISSIONS, DEFAULT_TRAITS

    traits = library_docs.list_docs(server.traits_home)
    perms = library_docs.list_docs(server.permissions_home)
    fallback = {"traits": [t for t in DEFAULT_TRAITS if t in {d["slug"] for d in traits}],
                "permissions": [p for p in DEFAULT_PERMISSIONS if p in {d["slug"] for d in perms}]}
    if not traits and not perms:
        return fallback
    workflow_note = ""
    if workflow_slug:
        wf = next((w for w in list_workflows(server.library_home)
                   if w["slug"] == workflow_slug), None)
        if wf:
            workflow_note = (f"\nCHOSEN WORKFLOW: {wf['slug']} — {wf['description']}\n"
                             f"Its suggested traits: {wf.get('includes') or '(none)'}")
    t_list = "\n".join(f"- {d['slug']}: {d['summary']}" for d in traits)
    p_list = "\n".join(f"- {d['slug']}: {d['summary']}"
                       + (f" [requires: {d['requires']}]" if d.get("requires") else "")
                       for d in perms)
    prompt = (
        "A new recurring LLM-agent routine is being created. Pick its TRAITS (reusable practice "
        "modules, adapted into the routine's own instructions at creation) and PERMISSIONS "
        "(conduct docs whose required capabilities the engine then enforces) from the catalogs "
        "below.\n\n"
        f"INSTRUCTION:\n{instruction}\n{workflow_note}\n\n"
        f"TRAITS:\n{t_list}\n\nPERMISSIONS:\n{p_list}\n\n"
        "Guidance: include ask-policy and ledger-discipline for almost everything. "
        "Pick permissions conservatively: only what the task clearly needs (e.g. communication "
        "only if it must reach the user outside the web UI; run-history only if runs build on "
        "each other's details beyond the last summary; shell almost never).\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(TRAITS_PERMS_SCHEMA)
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model,
                                           schema=TRAITS_PERMS_SCHEMA, temperature=ref.temperature,
                                           timeout=120,
                                           purpose="Suggest traits & permissions", kind="suggest")
        except Exception:
            return fallback
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, TRAITS_PERMS_SCHEMA)
            known_t = {d["slug"] for d in traits}
            known_p = {d["slug"] for d in perms}
            return {"traits": [t for t in obj.get("traits", []) if t in known_t],
                    "permissions": [p for p in obj.get("permissions", []) if p in known_p]}
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
    return fallback


def suggest_tags(server: ServerConfig, instruction: str) -> list[str]:
    """Suggest exactly 3 tags for a new routine. Reuse the existing vocabulary wherever a tag
    fits; coin a new tag only for a genuinely uncovered facet, never a synonym of an existing one.
    Returns [] if no generator endpoint answers (the caller falls back to manual entry).
    """
    vocab = existing_tags(server)
    prompt = (
        "Assign exactly THREE lowercase kebab-case tags to a new recurring LLM-agent routine so it "
        "can be filtered alongside the others. The three tags should capture its domain, its main "
        "capability, and its target/medium.\n"
        "STRONGLY prefer tags from the EXISTING vocabulary below — reuse a tag whenever it fits. "
        "Coin a NEW tag only when no existing tag captures a facet, and NEVER coin a synonym or "
        "near-duplicate of one already in the vocabulary (e.g. no 'messaging' when 'communication' "
        "exists, no 'automation' when 'browser' exists).\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        f"EXISTING VOCABULARY ({len(vocab)} tags): {', '.join(vocab) or '(none yet)'}\n\n"
        "Reply with ONLY one JSON object matching this schema (no prose):\n"
        + json.dumps(TAGS_SCHEMA)
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model, schema=TAGS_SCHEMA,
                                           temperature=ref.temperature, timeout=120,
                                           purpose="Suggest tags", kind="suggest")
        except Exception:
            return []
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, TAGS_SCHEMA)
            return normalize_tags(obj.get("tags", []))
        except SchemaViolation as exc:
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
    return []
