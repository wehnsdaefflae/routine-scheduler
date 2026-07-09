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
    endpoint, ref = EndpointRegistry(server).for_role("subcall", {})
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
