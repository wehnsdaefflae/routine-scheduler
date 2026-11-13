"""What the `openai` decision protocol puts in `input`, for every shape of evidence.

The field takes a string OR a message list (`endpoints/decisions_openai` docstring), so a
string whose whole content parses as a JSON object or array is ambiguous on the wire. The
gateway serving this instance's DEFAULT decision model refuses such a request outright:
measured 2026-11-13 against `otari-decisions`, a 62-character dict came back `HTTP 400
{"detail":"The provider rejected the request as invalid"}` while a 2,600-character prose
string of the same subject was answered, and the identical dict content prefixed with
`EVIDENCE (JSON):` was answered as well.

That made every structured-evidence `decide` call fail on the default model — including every
call carrying text `files`, because `decideaction._gather` folds those into a dict — while
`jev` worked throughout, the systemone protocol sending the object raw as `state`. These tests
pin the announcement and, just as importantly, pin that ordinary text is still sent untouched:
a prefix on every payload would change what every existing caller's model reads.
"""

from __future__ import annotations

import json

import pytest

from rsched.config.decisionconf import DecisionEndpointConfig
from rsched.endpoints.decisions import DecisionOption, DecisionQuestion
from rsched.endpoints.decisions_openai import (
    EVIDENCE_HEADER,
    OpenAIDecisions,
    evidence_text,
)

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
