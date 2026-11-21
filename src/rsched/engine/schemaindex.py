"""The ACTION SCHEMA as a compact FIELD INDEX — the prompt's half of a contract the wire
already carries.

Every transport this instance speaks enforces `loop.action_schema` STRUCTURALLY (Anthropic as
a tool's `input_schema`, OpenAI-compatible as strict `response_format`),
and the descriptions inside it are what the model reads there. Dumping the same object into
the system prompt paid for the contract twice (about a quarter of a typical prompt).

The one thing the schema does NOT encode: `ACTION_SCHEMA` is FLAT — only `say` and `kind` are
`required` — so nothing says which fields belong to which kind. `actions.KIND_FIELDS` does,
and `validate_action` builds its allowed-field set from the same map. So the prompt carries
the INDEX (what is callable, with which fields) and the wire carries the PROSE. A field the
model gets wrong is taught at that moment by `schema_guard.retry_message` with
`schemamemo.field_help`.

An endpoint whose `schema_mode` is `none` requests no grammar, so only the
prompt states the contract; `chain_carries` asks this of EVERY chain member and composes the
full dump if any is unenforced, because the prompt is a caching contract (`window.py`) and
cannot be rewritten later. A mid-run shed (`loop._schema_off`, `loop._shed_schema_turns`) is
handled where it happens, by an ENGINE NOTE carrying the full projection — an APPEND, which
the caching contract allows.
"""

from __future__ import annotations

import json

from .actions import KIND_FIELDS

#: Fields that ride every kind, stated once by the harness instead of per kind.
UNIVERSAL = ("say", "kind", "note", "role_swap", "remind", "remind_feedback", "background")

_HEAD = ("# ACTION SCHEMA (your every reply matches this)\n"
         "Your reply is ONE JSON object. `say` and `kind` are required on every action; "
         "`note`, `role_swap` and `background` are optional on any action that takes them. "
         "Every field's meaning is in the `action` tool's own schema, which this request "
         "carries and the endpoint enforces — the fields below are which ones each kind "
         "takes:\n")


#: The `schema_mode` values whose request carries the SCHEMA OBJECT ITSELF, descriptions
#: included. `none` sends no schema. The `anthropic` wire has no mode and
#: ALWAYS offers the schema as a tool's `input_schema`, so no `schema_mode` at all carries it.
_PROSE_MODES = ("json_schema",)


def carried_on_the_wire(endpoint) -> bool:
    """Whether `endpoint`'s schema'd call puts the schema's own descriptions in front of the
    model, so the composed prompt need not repeat them. `InstrumentedEndpoint.__getattr__`
    passes `schema_mode` through, so the wrapper needs nothing of its own.
    """
    mode = getattr(endpoint, "schema_mode", None)
    return True if mode is None else mode in _PROSE_MODES


def chain_carries(chain) -> bool:
    """True when EVERY member of a failover chain carries it.

    The whole chain, not the serving head: `engine/degrade.py` advances down it mid-turn and
    the composed prompt cannot be rewritten afterwards (caching contract, `engine/window.py`).
    An empty or unreadable chain answers False, so the fallback is always the full text.
    """
    members = list(chain or ())
    return bool(members) and all(carried_on_the_wire(ep) for ep, _ref in members)


def wire_enforces(ctx) -> bool:
    """Whether THIS RUN's ORDINARY serving chain carries the schema on the wire.

    The `main` chain only — including its transitive fallbacks, because `degrade` advances
    down it mid-turn and the composed prompt can never be rewritten afterwards.

    NOT the `uncensored` chain, deliberately: it is typically served schema-free, so asking
    about it would answer False for every routine. It serves only while a refusal back-off
    window is open, which the ENGINE sees as it happens — `turn_needs_the_contract` appends
    the full projection then, as an ENGINE NOTE.

    Defensive by construction: no registry, an unresolvable role, or any endpoint error
    answers False, so the prompt falls back to carrying the whole contract.
    """
    cached = getattr(ctx, "wire_enforces_schema", None)
    if cached is not None:
        return cached          # one chain resolution per run: the composer and loopsetup both ask
    registry = getattr(ctx, "registry", None)
    routine = getattr(ctx, "routine", None)
    if registry is None or routine is None:
        return False
    models = getattr(routine, "models", None) or {}
    efforts = getattr(routine, "efforts", None) or {}
    try:
        answer = chain_carries(registry.for_model_chain("main", models, efforts))
    except Exception:
        return False
    try:
        ctx.wire_enforces_schema = answer
    except Exception:          # a frozen or slotted stand-in: answering is what matters
        pass
    return answer


def turn_needs_the_contract(loop, endpoint) -> bool:
    """Whether THIS turn is about to be served with neither the wire's schema nor the prompt's.

    Three ways that happens to a run whose prompt holds only the index:

    * the uncensored takeover hands this turn to a chain the composer did not weigh;
    * `loop._schema_off` — repeat-streak shedding has dropped the provider grammar for the
      rest of the run (`loop._repeat_warning`);
    * `loop._shed_schema_turns` — the next reply alone runs schema-free.

    Asked at the head of every turn (`turntakeover.serve_turn_head`). Answers False once the
    note has been appended — it stays in the prompt, and re-appending every turn would cost
    what the index saves.
    """
    if getattr(loop, "_schema_contract_sent", False):
        return False
    if not getattr(loop, "_schema_is_index", False):
        return False          # the prompt carries the full contract already
    return bool(getattr(loop, "_schema_off", False)
                or getattr(loop, "_shed_schema_turns", 0)
                or not carried_on_the_wire(endpoint))


def send_the_contract(loop, projection: dict, *, before: int | None = None) -> None:
    """Append the full projection as an ENGINE NOTE, once per run.

    `before` is forwarded for the retry-loop caller, whose note must not displace the
    correction from the end of the prompt (`request_contract`).
    """
    from . import enginenote
    loop._schema_contract_sent = True
    enginenote.append(loop, (
        "this turn is served without the structured-output grammar, so the action contract "
        "is stated here in full instead of as the field index in your prompt. It is the same "
        "contract either way — the schema your reply must match:\n\n"
        + json.dumps(projection, indent=1)), before=before)


def request_contract(loop, endpoint, schema: dict | None) -> None:
    """Keep the contract available before EVERY actual completion request.

    The turn-head check cannot see a schema shed inside the retry loop or a mid-turn
    endpoint switch. Consult the schema this attempt will send, retaining the existing
    once-per-run note and the cached prompt prefix.

    Position matters: inside the retry loop the last user message is the CORRECTION, which
    must stay last (D287). So the note goes in BEFORE this turn's debris block when there is
    one, and is appended when there is not (the turn-head case).
    """
    from . import turndebris
    if (getattr(loop, "_schema_is_index", False)
            and not getattr(loop, "_schema_contract_sent", False)
            and (schema is None or not carried_on_the_wire(endpoint))):
        send_the_contract(loop, loop.action_schema,
                          before=turndebris.head_index(loop))


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

    The caller passes the PROJECTION (`kindsurface.effective_kinds`) — the same narrowing the
    enforced schema gets, so the two cannot disagree.
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
    """The whole projection as JSON, for a run whose wire does not enforce it."""
    return ("# ACTION SCHEMA (your every reply matches this)\n"
            + json.dumps(schema, indent=1))
