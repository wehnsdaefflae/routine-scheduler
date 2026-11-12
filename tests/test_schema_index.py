"""The ACTION SCHEMA section: a compact field index where the wire enforces the schema, the
full dump where it does not.

Measured 2026-11-12 over 21 live run captures: the dumped section averaged 28,189 chars of a
108,666-char prompt, 26,143 of them `description` prose for a schema `endpoint.complete`
already puts on the wire — enforced, descriptions included. These tests hold the two halves
that make dropping the copy safe: the index still carries what the FLAT schema cannot (which
fields belong to which kind), and the full dump comes back for every wire that enforces
nothing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from helpers import run_context, server_config
from rsched.engine import schemaindex
from rsched.engine.actions import KIND_FIELDS
from rsched.engine.actionschema import KINDS
from rsched.engine.composer import state_digest, system_sections
from rsched.engine.kindsurface import schema_for_kinds
from rsched.grantpolicy import GrantPolicy

#: "this chain was never configured", distinct from a mode of `None` (the anthropic wire).
_MISSING = object()


class _Ref:
    endpoint = "otari"
    model = "claude-opus-5"
    name = "Opus"


class _Endpoint:
    """An endpoint stand-in carrying only what the predicate reads: its schema mode. A mode
    of `None` is the anthropic wire, which has no such attribute at all.
    """

    def __init__(self, mode: str | None = None):
        if mode is not None:
            self.schema_mode = mode


class _Registry:
    def __init__(self, mode: str | None = None, *, uncensored: object = "same"):
        self._mode = mode
        self._uncensored = mode if uncensored == "same" else uncensored

    def for_model_chain(self, kind, models, efforts):
        if kind == "uncensored":
            if self._uncensored is _MISSING:
                raise RuntimeError("no model configured for 'uncensored'")
            return [(_Endpoint(self._uncensored), _Ref())]
        return [(_Endpoint(self._mode), _Ref())]


def _routine_dir(root: Path) -> Path:
    d = root / "routines" / "budget"
    (d / "state").mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "Budget", "slug": "budget", "enabled": True, "description": "fixed",
         "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin"}}), encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n", encoding="utf-8")
    return d


def _ctx_with(registry):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        return run_context(_routine_dir(root), "20261025-070000",
                           server=server_config(libraries_home=root / "libraries"),
                           registry=registry)


def _sections(registry) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        d = _routine_dir(root)
        ctx = run_context(d, "20261025-070000",
                          server=server_config(libraries_home=root / "libraries"),
                          registry=registry)
        ctx.grants = GrantPolicy(actions=frozenset(KINDS), reminders="local")
        return dict(system_sections(ctx, "## Run flow", "task", state_digest(d, [], []), []))


# --- the predicate -------------------------------------------------------------------

@pytest.mark.parametrize(("mode", "carries"), [
    (None, True),              # the anthropic wire: always a tool's input_schema
    ("json_schema", True),     # strict response_format
    ("ollama_native", True),   # Ollama's `format` — real constrained decoding
    ("json_object", False),    # "any JSON": no schema is sent at all
    ("none", False),           # nothing requested
])
def test_which_wires_carry_the_schema_prose(mode, carries):
    assert schemaindex.carried_on_the_wire(_Endpoint(mode)) is carries


def test_one_unenforced_chain_member_disqualifies_the_whole_chain():
    """`degrade` advances down the chain MID-TURN and the composed prompt cannot be rewritten
    afterwards (it is a caching contract), so the question is the whole chain, not its head.
    """
    good = (_Endpoint("json_schema"), _Ref())
    bad = (_Endpoint("none"), _Ref())
    assert schemaindex.chain_carries([good, good]) is True
    assert schemaindex.chain_carries([good, bad]) is False
    assert schemaindex.chain_carries([]) is False


def test_the_uncensored_chain_does_not_decide_the_prompt():
    """Measured 2026-11-12 and the reason the predicate is MAIN-chain only: on this instance
    `uncensored` is `otari-openai-plain`, `schema_mode: none`, because its model refuses
    `response_format`. Weighing that chain at composition answered False for every routine
    here — a cut that saves nothing in production while passing every test. The takeover is
    rare and VISIBLE to the engine when it happens, so it is served by an engine note instead
    (`turn_needs_the_contract`), and the prompt is composed for the ordinary case.
    """
    assert schemaindex.wire_enforces(_ctx_with(_Registry("json_schema"))) is True
    assert schemaindex.wire_enforces(
        _ctx_with(_Registry("json_schema", uncensored="none"))) is True
    assert schemaindex.wire_enforces(
        _ctx_with(_Registry("json_schema", uncensored=_MISSING))) is True
    # the MAIN chain still decides it
    assert schemaindex.wire_enforces(_ctx_with(_Registry("none"))) is False


# --- the turn that arrives with no grammar -------------------------------------------

class _Loop:
    def __init__(self, **fields):
        self.action_schema = {"type": "object", "properties": {}}
        self.messages: list[dict] = []
        self.__dict__.update(fields)


@pytest.mark.parametrize(("fields", "mode", "needed"), [
    ({"_schema_is_index": True}, "json_schema", False),   # the wire states it
    ({"_schema_is_index": True}, None, False),            # anthropic: a tool's input_schema
    ({"_schema_is_index": True}, "none", True),            # nothing states it
    ({"_schema_is_index": True}, "json_object", True),     # no schema is sent
    ({"_schema_is_index": True, "_schema_off": True}, "json_schema", True),
    ({"_schema_is_index": True, "_shed_schema_turns": 1}, "json_schema", True),
    ({"_schema_is_index": False}, "none", False),          # the prompt already carries it
    ({"_schema_is_index": True, "_schema_contract_sent": True}, "none", False),
])
def test_when_a_turn_needs_the_contract_appended(fields, mode, needed):
    assert schemaindex.turn_needs_the_contract(
        _Loop(**fields), _Endpoint(mode)) is needed


def test_the_contract_is_appended_once_and_carries_the_whole_projection():
    """An action definition must never be unloaded after a run has used it, and re-appending
    the projection every turn would be the cost the cut removed — so it is sent once.
    """
    loop = _Loop(_schema_is_index=True)
    sent: list[str] = []

    class _Transcript:
        def event(self, *_a, **_k):
            pass

    class _Ctx:
        transcript = _Transcript()

    loop.ctx = _Ctx()
    projection = schema_for_kinds(["util", "finish"], reminders=False)
    schemaindex.send_the_contract(loop, projection)
    sent = [m["content"] for m in loop.messages]
    assert len(sent) == 1 and sent[0].startswith("ENGINE NOTE:")
    assert json.dumps(projection, indent=1) in sent[0]
    assert loop._schema_contract_sent is True
    assert schemaindex.turn_needs_the_contract(loop, _Endpoint("none")) is False


def test_no_registry_falls_back_to_the_full_contract():
    """Every unreadable case answers False: a prompt that guessed wrong would leave a run with
    neither a grammar nor a contract.
    """
    assert schemaindex.wire_enforces(_ctx_with(None)) is False
    assert schemaindex.wire_enforces(object()) is False


# --- what the index must still carry -------------------------------------------------

def test_the_index_names_every_kind_and_its_own_fields():
    """`ACTION_SCHEMA` is FLAT — its only `required` entries are `say` and `kind` — so nothing
    in the enforced object says which fields belong to which kind. That is what the index is
    for, and it is read off `KIND_FIELDS`, the same map `validate_action` uses.
    """
    text = schemaindex.index(list(KINDS))
    for kind in KINDS:
        assert f"`{kind}`" in text, f"the index omits the kind {kind!r}"
    # a kind's own required field is named, and a universal one is not repeated per kind
    assert "`util`: name" in text
    assert "`write_file`: path content" in text
    assert text.count("say") == 1, "a universal field must be stated once, not 34 times"


def test_the_index_is_projected_like_the_schema():
    """A run reads only the kinds it may emit — the same narrowing the enforced schema gets."""
    text = schemaindex.index(["util", "finish"])
    assert "`util`" in text and "`finish`" in text
    assert "`shell`" not in text and "`spawn`" not in text


def test_the_index_is_an_order_of_magnitude_smaller_than_the_dump():
    full = schemaindex.full(schema_for_kinds(None, reminders=True))
    index = schemaindex.index(list(KINDS))
    assert len(index) < len(full) / 10, (
        f"the index is {len(index)} chars against the dump's {len(full)}: the saving is the "
        "whole point of the section")


# --- the composed section ------------------------------------------------------------

def test_the_composed_section_is_the_index_on_an_enforcing_wire():
    schema = _sections(_Registry("json_schema"))["schema"]
    assert schema.startswith("# ACTION SCHEMA (your every reply matches this)")
    assert "- `util`: name" in schema
    # the dump's own shape must be gone: no JSON object, no description prose
    assert '"properties"' not in schema
    assert len(schema) < 4000, f"the section is {len(schema)} chars — the dump is still in it"


def test_the_composed_section_is_the_full_dump_on_an_unenforced_wire():
    schema = _sections(_Registry("none"))["schema"]
    assert '"properties"' in schema, "a wire that enforces nothing must carry the contract"
    parsed = json.loads(schema.split("\n", 1)[1])
    assert parsed == schema_for_kinds(None, reminders=True)


def test_the_turn_head_is_wired_to_send_it():
    """The predicate proves nothing if no turn asks it. `turntakeover.serve_turn_head` is the
    one function that knows which endpoint is about to serve, so both of its branches — the
    ordinary chain and the uncensored takeover — go through the same check.
    """
    from rsched.engine import turntakeover
    source = Path(turntakeover.__file__).read_text(encoding="utf-8")
    assert source.count("_contract_if_unenforced(loop, endpoint)") == 3, (
        "both branches of serve_turn_head must ask, and the helper must exist: a turn served "
        "with no grammar and only the field index has no statement of the contract at all")
    assert "schemaindex.send_the_contract" in source


def test_the_enforced_object_and_the_index_come_from_one_source():
    """The prompt's index and the wire's schema must describe the same surface: both are
    derived from the projection, so a kind in one is a kind in the other.
    """
    projection = schema_for_kinds(["util", "finish", "read_file"], reminders=False)
    enum = set(projection["properties"]["kind"]["enum"])
    text = schemaindex.index(sorted(enum))
    for kind in enum:
        assert f"`{kind}`" in text
    assert set(KIND_FIELDS) >= enum
