"""The ACTION SCHEMA as a compact FIELD INDEX — the prompt's half of a contract the wire
already carries.

## Why the dump was paid twice

`completion.next_action` hands `loop.action_schema` to `endpoint.complete(..., schema=…)`, and
every transport this instance speaks enforces it STRUCTURALLY: the Anthropic adapter offers it
as a tool (`{"name": "action", "input_schema": schema}`) held to one call, the OpenAI-compatible
one as `response_format: {"type": "json_schema", "strict": True}`, Ollama as `format` — real
constrained decoding. The descriptions inside that object are what the model reads there.

`composer.system_sections` ALSO dumped the same object into the system prompt as indent=1 JSON.
Measured over 21 live run captures (`runs/<ts>/prompt-sections.json`, R3341's boot capture) on
2026-11-12, that section averaged **28,189 characters** — 26% of a 108,666-char prompt — of
which **26,143 were `description` prose** and only ~7,800 the structure. So the contract
travelled twice in one request, and the copy the provider enforces is the one the model cannot
ignore.

## What the prompt still owes

One thing the schema object genuinely does NOT encode: `ACTION_SCHEMA` is FLAT — its only
`required` entries are `say` and `kind` — so nothing in it says which fields belong to which
kind. `actions.KIND_FIELDS` knows exactly, and it is the same map `validate_action` builds its
allowed-field set from. Rendered as one line per kind that is **1,442 characters** for all 34
kinds, against the 21,421 of `kindprose.kind_bullets` + 28,189 of dump that used to carry it.

So the prompt carries the INDEX (what is callable, and with which fields) and the wire carries
the PROSE (what each field means, enforced). A field the model gets wrong is taught at the
moment it is wrong, by `schema_guard.retry_message` with `schemamemo.field_help` — which already
quotes the schema's own words for the named field, inside the existing retry cycle.

## The one case where the prompt must still carry the whole thing

An endpoint whose `schema_mode` is `none` or `json_object` requests no grammar, so nothing but
the prompt states the contract; and the engine itself SHEDS the grammar mid-run
(`loop._schema_off` after two repeat-streak sheds, `loop._shed_schema_turns` for one turn).
`enforced()` answers the first question per endpoint, and the composer asks it of EVERY member
of the run's failover chain — a chain that can fail over to an unenforced endpoint is composed
the old way, because the prompt cannot be rewritten later: it is a caching contract
(`window.py`), and a cold window of this size has cost over $100 on this instance.

A mid-run shed is the case the composer cannot foresee. It is handled where it happens, by an
ENGINE NOTE carrying the full projection (`schemashed.py`) — an APPEND, which the caching
contract allows, rather than a rewrite of what was already sent.
"""

from __future__ import annotations

import json

from .actions import KIND_FIELDS

#: Fields that ride every kind, so naming them per kind would be 34 copies of one line. The
#: harness states them once; `kindsurface._UNIVERSAL_FIELDS` is the same set plus the two
#: capability-gated reminder fields, which are listed there because they are PROJECTED.
UNIVERSAL = ("say", "kind", "note", "role_swap", "remind", "remind_feedback", "background")

_HEAD = ("# ACTION SCHEMA (your every reply matches this)\n"
         "Your reply is ONE JSON object. `say` and `kind` are required on every action; "
         "`note`, `role_swap` and `background` are optional on any action that takes them. "
         "Every field's meaning is in the `action` tool's own schema, which this request "
         "carries and the endpoint enforces — the fields below are which ones each kind "
         "takes:\n")


#: The `schema_mode` values whose request carries the SCHEMA OBJECT ITSELF, descriptions
#: included: `json_schema` (strict `response_format`) and `ollama_native` (Ollama's `format`
#: field — real constrained decoding). `json_object` asks for "any JSON" and sends no schema;
#: `none` asks for nothing. The `anthropic` wire has no mode: a schema'd call there is ALWAYS
#: offered as a tool's `input_schema`, so an endpoint with no `schema_mode` attribute at all
#: carries it (`endpoints/anthropic_api.complete`).
_PROSE_MODES = ("json_schema", "ollama_native")


def carried_on_the_wire(endpoint) -> bool:
    """Whether `endpoint`'s schema'd call puts the schema's own descriptions in front of the
    model, so the composed prompt need not repeat them.

    Asked of an endpoint rather than implemented on each adapter because both adapter files
    sit AT the 350-line bar: the knowledge is one line of `schema_mode` vocabulary and belongs
    with the section that acts on it. `InstrumentedEndpoint.__getattr__` passes the attribute
    through, so the wrapper needs nothing of its own.
    """
    mode = getattr(endpoint, "schema_mode", None)
    return True if mode is None else mode in _PROSE_MODES


def chain_carries(chain) -> bool:
    """True when EVERY member of a failover chain carries it.

    The whole chain, not the serving head: `engine/degrade.py` advances down it mid-turn and
    the composed prompt cannot be rewritten afterwards — it is a caching contract
    (`engine/window.py`), and a cold window of this size has cost over $100 on this instance.
    An empty or unreadable chain answers False, so the fallback is always the full text.
    """
    members = list(chain or ())
    return bool(members) and all(carried_on_the_wire(ep) for ep, _ref in members)


def wire_enforces(ctx) -> bool:
    """Whether THIS RUN's ORDINARY serving chain carries the schema on the wire.

    The `main` chain only — including its transitive fallbacks, because `degrade` advances
    down it mid-turn and the composed prompt can never be rewritten afterwards.

    NOT the `uncensored` chain, and that is a deliberate correction measured on 2026-11-12:
    on this instance `uncensored` is served by `otari-openai-plain`, whose `schema_mode` is
    `none` precisely because its model refuses `response_format`. Asking about both chains
    therefore answered False for EVERY routine here — a cut that saves nothing in production
    while passing every test. The uncensored chain serves only while a refusal back-off window
    is open, which is rare and, crucially, is something the ENGINE sees at the moment it
    happens: `turn_needs_the_contract` is asked at the head of every turn and the full
    projection is appended then, as an ENGINE NOTE. An append is what the caching contract
    allows; composing for the rare case was paying for it on every turn of every run.

    Defensive by construction: no registry (the unit fixtures), an unresolvable role, or any
    endpoint error answers False, so the prompt falls back to carrying the whole contract. A
    measurement that guessed wrong here would cost a run its action grammar.
    """
    registry = getattr(ctx, "registry", None)
    routine = getattr(ctx, "routine", None)
    if registry is None or routine is None:
        return False
    models = getattr(routine, "models", None) or {}
    efforts = getattr(routine, "efforts", None) or {}
    try:
        return chain_carries(registry.for_model_chain("main", models, efforts))
    except Exception:
        return False


def turn_needs_the_contract(loop, endpoint) -> bool:
    """Whether THIS turn is about to be served with neither the wire's schema nor the prompt's.

    Three ways that happens to a run whose prompt holds only the index:

    * the uncensored takeover hands this turn to a chain the composer did not weigh;
    * `loop._schema_off` — repeat-streak shedding has dropped the provider grammar for the
      rest of the run (`loop._repeat_warning`);
    * `loop._shed_schema_turns` — the next reply alone runs schema-free.

    Asked at the head of every turn (`turntakeover.serve_turn_head`), so the contract arrives
    at the moment it is needed rather than being paid for on every turn against the chance.
    Answers False once the note has been appended: an action definition must never be unloaded
    after a run has used it, and re-appending the whole projection every turn would be the same
    cost the cut removed.
    """
    if getattr(loop, "_schema_contract_sent", False):
        return False
    if not getattr(loop, "_schema_is_index", False):
        return False          # the prompt carries the full contract already
    return bool(getattr(loop, "_schema_off", False)
                or getattr(loop, "_shed_schema_turns", 0)
                or not carried_on_the_wire(endpoint))


def send_the_contract(loop, projection: dict) -> None:
    """Append the full projection as an ENGINE NOTE, once per run."""
    from . import enginenote
    loop._schema_contract_sent = True
    enginenote.append(loop, (
        "this turn is served without the structured-output grammar, so the action contract "
        "is stated here in full instead of as the field index in your prompt. It is the same "
        "contract either way — the schema your reply must match:\n\n"
        + json.dumps(projection, indent=1)))


def section(schema: dict, kinds: list[str], ctx) -> str:
    """The prompt's ACTION SCHEMA section: the compact index where the wire carries the
    prose, the full dump where it does not. One decision, one place.
    """
    return index(kinds) if wire_enforces(ctx) else full(schema)


def _fields(kind: str) -> tuple[list[str], list[str]]:
    """One kind's own required and optional fields, universals removed."""
    required, optional = KIND_FIELDS[kind]
    return ([f for f in required if f not in UNIVERSAL],
            [f for f in optional if f not in UNIVERSAL])


def index(kinds: list[str]) -> str:
    """`kind: required [optional]`, one line per kind, in the caller's order.

    The caller passes the PROJECTION (`kindsurface.effective_kinds`), so a run reads only the
    kinds it may actually emit — the same narrowing the enforced schema gets, from the same
    source, which is why the two cannot disagree.
    """
    lines = []
    for kind in kinds:
        if kind not in KIND_FIELDS:
            continue
        required, optional = _fields(kind)
        tail = f" [{' '.join(optional)}]" if optional else ""
        lines.append(f"- `{kind}`{': ' + ' '.join(required) if required else ':'}{tail}")
    return _HEAD + "\n".join(lines)


def full(schema: dict) -> str:
    """The whole projection as JSON — what the prompt carried before, and still carries for a
    run whose wire does not enforce it.
    """
    return ("# ACTION SCHEMA (your every reply matches this)\n"
            + json.dumps(schema, indent=1))
