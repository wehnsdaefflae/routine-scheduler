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
                     "description": "true when no listed workflow fits well and a new one should be drafted"},
        "new_workflow_hint": {"type": "string",
                              "description": "when none_fit: one paragraph sketching the missing workflow"},
    },
}

# Workflows tagged 'meta' are internal machinery (wizard, library maintenance, self-audit) —
# excluded from user-facing suggestion. This replaces the old hardcoded name set with the tag.
INTERNAL_TAG = "meta"


def suggest(server: ServerConfig, instruction: str) -> dict:
    candidates = [w for w in list_workflows(server.library_home)
                  if INTERNAL_TAG not in (w.get("tags") or []) and w["status"] == "stable"]
    if not candidates:
        return {"suggestions": [], "none_fit": True,
                "new_workflow_hint": "library has no stable workflows yet"}
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
                                       schema=SUGGEST_SCHEMA, timeout=120)
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

    from .. import fragments_lib, utils_lib
    tags: set[str] = set()
    for w in list_workflows(server.library_home):
        tags.update(w.get("tags") or [])
    for f in fragments_lib.list_fragments(server.fragments_home):
        tags.update(f.get("tags") or [])
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


def suggest_tags(server: ServerConfig, instruction: str) -> list[str]:
    """Suggest exactly 3 tags for a new routine. Reuse the existing vocabulary wherever a tag
    fits; coin a new tag only for a genuinely uncovered facet, never a synonym of an existing one.
    Returns [] if no generator endpoint answers (the caller falls back to manual entry)."""
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
        "Reply with ONLY one JSON object matching this schema (no prose):\n" + json.dumps(TAGS_SCHEMA)
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    messages = [{"role": "user", "content": prompt}]
    for _attempt in range(2):
        try:
            completion = endpoint.complete(messages, model=ref.model, schema=TAGS_SCHEMA, timeout=120)
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
