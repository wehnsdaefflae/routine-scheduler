"""What a decision protocol puts in its evidence field, for every shape of evidence — BOTH of
them, because the refusal these tests pin is the gateway's and not one wire shape's.

Measured against `otari-decisions`, which serves all three of this instance's decision models
over the `systemone` protocol: evidence given as a dict came back `HTTP 400 {"detail":"The
provider rejected the request as invalid"}` five times across 2026-11-13/14, while the
byte-exact announced string built from that same dict was answered by all three
(`gemini-decision` 0.068, `jev` 0.050, `local-qwen-vl8b` 0.000) and prose was answered
throughout. So every structured-evidence `decide` call failed on the default model — including
every call carrying text `files`, because `decideaction._gather` folds those into a dict.

The first fix (0.535.2) announced the evidence in the `openai` adapter only, reasoning from
`input`'s string-or-message-list ambiguity. **It changed nothing live**: no model on this
instance speaks `openai`, and `systemone` put the object raw into `state`. Hence
`test_both_protocols_announce_structured_evidence` — a pin on one adapter cannot speak for the
other. These tests also pin that ordinary text is still sent untouched: a prefix on every
payload would change what every existing caller's model reads.
"""

from __future__ import annotations

import json

import pytest

from rsched.config.decisionconf import DecisionEndpointConfig
from rsched.endpoints.decisions import (
    DEFAULT_BASE_URLS,
    EVIDENCE_HEADER,
    DecisionOption,
    DecisionQuestion,
    evidence_text,
    make_decision_endpoint,
)
from rsched.endpoints.decisions_openai import OpenAIDecisions
from rsched.endpoints.decisions_systemone import SystemOneDecisions

PLAIN = [
    "cannot log in",
    "Row R3368.\nSubject: the util crashed.\nIt was already fixed.",
    '{"id": "R3368"} — and this sentence makes it prose, not a JSON document',
    "42",
    '"a quoted string is valid JSON, and is not a document that collides"',
    "[not json after all",
    "{oops",
    "",
]


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_text_reaches_the_wire_byte_for_byte(text: str) -> None:
    """Only the ambiguous shape is announced. Everything else is what the caller wrote: a
    prefix on every payload would silently change the evidence every current caller's model
    reads, which is the one thing this fix must not do."""
    assert evidence_text(text) == text


@pytest.mark.parametrize("evidence", [
    {"id": "R3368", "subject": "the util crashed"},
    ["one", "two"],
    {},
    [],
])
def test_structured_evidence_is_announced_so_input_cannot_read_as_a_message_list(
        evidence: dict | list) -> None:
    out = evidence_text(evidence)
    assert out.startswith(EVIDENCE_HEADER), out[:40]
    body = out[len(EVIDENCE_HEADER):]
    assert json.loads(body) == evidence, "the evidence itself must survive the announcement"


def test_a_callers_own_bare_json_string_is_announced_too() -> None:
    """A run that reads a JSON file and passes its TEXT hits the identical refusal, so the
    guard reads the content rather than the Python type it arrived as."""
    raw = '{"id": "R3368", "subject": "the util crashed"}'
    assert evidence_text(raw) == EVIDENCE_HEADER + raw


def test_the_announcement_is_not_applied_twice() -> None:
    """Idempotence matters because the header itself makes the text no longer a bare JSON
    document, so a second pass must leave it alone."""
    once = evidence_text({"id": "R3368"})
    assert evidence_text(once) == once


def _questions() -> list[DecisionQuestion]:
    return [DecisionQuestion(name="answer", question="Is it ours?", type="yes_no", options=[]),
            DecisionQuestion(name="owner", question="Who owns it?", type="choice",
                             options=[DecisionOption("a", "us"), DecisionOption("b", "them")])]


def _adapter() -> OpenAIDecisions:
    return OpenAIDecisions(DecisionEndpointConfig(name="probe", protocol="openai",
                                                  base_url="http://example.invalid"))


def test_the_request_body_carries_the_announced_evidence_as_input() -> None:
    body = _adapter().body({"id": "R3368"}, [], _questions(), "gpt-6-luna")
    assert isinstance(body["input"], str)
    assert body["input"].startswith(EVIDENCE_HEADER)
    assert [q["name"] for q in body["questions"]] == ["answer", "owner"]


def test_a_text_only_body_sends_the_string_form_and_no_image_parts() -> None:
    """The dict branch must not turn a text call into the multimodal list form: that shape is
    for images, and sending it without any would change the request for every text caller."""
    body = _adapter().body({"id": "R3368"}, [], _questions(), "gpt-6-luna")
    assert isinstance(body["input"], str), "a text-only call stays in the string form"


def _systemone() -> SystemOneDecisions:
    return SystemOneDecisions(DecisionEndpointConfig(name="probe", protocol="systemone",
                                                     base_url="http://example.invalid"))


def test_systemone_announces_its_state_rather_than_sending_a_raw_object() -> None:
    """The live path on this instance: every configured decision model speaks `systemone`, and
    the gateway refuses a `state` that is a JSON object (HTTP 400, measured five times)."""
    body = _systemone().body({"id": "R3368"}, [], _questions(), "gemini:gemini-decision-1")
    assert isinstance(body["state"], str), "a raw object here is the refusal this test pins"
    assert body["state"].startswith(EVIDENCE_HEADER)
    assert json.loads(body["state"][len(EVIDENCE_HEADER):]) == {"id": "R3368"}


def test_every_protocol_announces_structured_evidence() -> None:
    """A pin on ONE adapter cannot speak for the other: 0.535.2 announced in `openai` alone and
    fixed nothing live, because no model on this instance speaks it. Driven off the adapter
    table, so a protocol added later is held to the same rule."""
    fields = {"openai": "input", "systemone": "state"}
    assert set(fields) == set(DEFAULT_BASE_URLS), "a new protocol needs its field named here"
    for protocol, field in fields.items():
        cfg = DecisionEndpointConfig(name="probe", protocol=protocol,
                                     base_url="http://example.invalid")
        body = make_decision_endpoint(cfg).body({"id": "R3368"}, [], _questions(), "m")
        assert body[field].startswith(EVIDENCE_HEADER), f"{protocol} sends evidence unannounced"
