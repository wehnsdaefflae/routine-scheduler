"""The suggesters: what the console asks the SYSTEM MODEL on a person's behalf.

All the same shape — build a prompt, get one JSON object back, degrade quietly if it does not
come. Here: write a routine's description at create time (`generate_description`); the settings
recommender (`patterns/recommend.py`) asks through the same `_ask_json`. None of them may fail
the flow they sit in; a person is waiting on a form, and "pick it yourself" is always an
acceptable answer where "500" is not.
"""

from __future__ import annotations

import json

from ..config import ServerConfig
from ..endpoints import EndpointRegistry
from ..paths import read_yaml
from ..schema_guard import SchemaViolation, parse_reply, validate
from .library import list_workflows


def _ask_json(server: ServerConfig, prompt: str, schema: dict, *,
              purpose: str) -> tuple[dict | None, str]:
    """One schema-valid JSON object from the system model: `(obj, "")`, or `(None, why)`.

    Every suggester wants exactly this and nothing more: ask once, and if the reply does not
    satisfy the schema, show the model its own reply plus the violation and ask again. One
    retry, because a model that cannot produce the shape twice will not produce it on a third
    try either, and these calls sit in front of a person waiting on a form.

    `why` is `"unavailable"` (no system model, or the endpoint failed), `"empty"` (the model
    answered with nothing at all — F921: distinct from a bad answer, since no reworded prompt
    fixes a silent model) or `"malformed"` (it
    answered twice and neither answer fit the schema). Both mean "pick it yourself", so most
    callers drop it — but they are different facts about the instance, one a configuration
    problem and one a model problem.

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
        if completion.is_empty():
            # F921: nothing came back. Reporting "malformed" would describe a reply that does
            # not exist, and a caller retrying the PROMPT cannot fix a silent model.
            return None, "empty"
        try:
            obj = _checked(completion, schema)
        except SchemaViolation as exc:
            shown = completion.text or json.dumps(completion.parsed, ensure_ascii=False)
            messages.append({"role": "assistant", "content": shown[:2000]})
            messages.append({"role": "user", "content":
                             f"Invalid: {exc}. Reply again with ONLY the JSON object."})
        else:
            return obj, ""
    return None, "malformed"


def _checked(completion, schema: dict) -> dict:
    """The reply's object, held to `schema` however it arrived. A provider's native schema
    mode (Anthropic tool use) hands back `parsed` without enforcing the schema, so it is
    validated exactly like an object extracted from the text.
    """
    if completion.parsed is None:
        return parse_reply(completion.text, schema)
    if problems := validate(completion.parsed, schema):
        raise SchemaViolation(problems)
    return completion.parsed


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
    """Compact list of the routines that already exist — slug · name · tags — so a generated
    description can name real inter-routine dependencies instead of inventing them. Bounded
    (60 rows) so a large instance never blows the prompt; best-effort per file: one that does
    not parse to a mapping is passed over.
    """
    import yaml

    lines: list[str] = []
    for y in sorted(server.routines_home.glob("*/routine.yaml")):
        try:
            cfg = read_yaml(y, {})
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(cfg, dict):
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
    routines (which it feeds, consumes from, or shares a store with). Replaces
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
        "or shares a store with.\n\n"
        f"ROUTINE NAME: {name}{wf_note}\n\nTASK:\n{task[:8000]}{sib_note}\n\n"
        "Keep it factual and specific — 3 to 6 sentences, at most ~700 characters. Reply with "
        "ONLY one JSON object matching this schema (no prose):\n" + json.dumps(DESCRIBE_SCHEMA)
    )
    obj, _why = _ask_json(server, prompt, DESCRIBE_SCHEMA, purpose="Generate routine description")
    if obj is None:
        return name                          # no system model, or no usable reply — keep the name
    return str(obj.get("description") or "").strip() or name

