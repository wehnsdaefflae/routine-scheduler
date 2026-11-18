"""F903: every actual schema-free request retains the full action contract.

These pins drive the shared completion loop, not a transport-specific helper. The
ordinary request keeps its compact prefix; a within-turn shed or failover appends the
existing persistent contract note before the first request that needs it.
"""

from __future__ import annotations

import copy
import json

import pytest

from rsched.endpoints import failover
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine import schemaindex
from rsched.engine.completion import next_action
from test_loop_referral import _FakeEndpoint, _FakeRegistry, _loop
from test_oversize_prompt import _ChainRegistry

VALID = {"kind": "read_file", "say": "Read the recipe.", "path": "main.md"}
INVALID = {"kind": "util", "say": "Missing the utility name."}
OTHER_INVALID = {"kind": "write_file", "say": "Missing the contents.", "path": "x"}


class RecordingEndpoint(_FakeEndpoint):
    def __init__(self, completions, *, mode="json_schema"):
        super().__init__(completions)
        self.schema_mode = mode
        self.requests = []

    def complete(self, messages, **kwargs):
        self.requests.append((copy.deepcopy(messages), kwargs.get("schema")))
        return super().complete(messages, **kwargs)


def contract_notes(messages):
    return [m["content"] for m in messages
            if m["content"].startswith("ENGINE NOTE: this turn is served without")]


def prepare(loop, *, index=True):
    loop._schema_is_index = index
    loop._schema_contract_sent = False
    body = (schemaindex.index(["util", "read_file", "write_file"]) if index
            else schemaindex.full(loop.action_schema))
    loop.messages = [{"role": "user", "content": "kickoff\n" + body}]
    return copy.deepcopy(loop.messages[0])


@pytest.mark.parametrize("cause", ["invalid", "empty"])
@pytest.mark.parametrize("index", [True, False])
def test_last_attempt_gets_the_contract_before_the_schema_free_request(
        make_routine, cause, index):
    replies = ([INVALID, OTHER_INVALID] if cause == "invalid" else
               [Completion(text="", usage={}), Completion(text="", usage={})])
    endpoint = RecordingEndpoint([*replies, VALID])
    loop = _loop(make_routine, _FakeRegistry(endpoint, main_name=f"retry-{cause}-{index}"))
    prefix = prepare(loop, index=index)
    try:
        action, _ = next_action(loop)
    finally:
        failover.reset()
    assert action == VALID
    assert len(endpoint.requests) == 3
    assert [schema is None for _, schema in endpoint.requests] == [False, False, True]
    for position, (messages, _) in enumerate(endpoint.requests):
        assert messages[0] == prefix, "contract recovery must not rewrite the cached prefix"
        expected = int(index and position == 2)
        assert len(contract_notes(messages)) == expected, (
            "the schema-free retry must receive exactly one full contract note")
    notes = contract_notes(loop.messages)
    assert len(notes) == int(index), "the contract must survive removal of retry debris"
    if index:
        assert json.dumps(loop.action_schema, indent=1) in notes[0]


def test_recalled_error_gets_the_contract_on_the_second_attempt_and_only_once(make_routine):
    endpoint = RecordingEndpoint([INVALID, VALID, INVALID, VALID, VALID])
    loop = _loop(make_routine, _FakeRegistry(endpoint, main_name="recalled-contract"))
    prefix = prepare(loop)
    try:
        first, _ = next_action(loop)
        loop.ctx.turn += 1
        second, _ = next_action(loop)
        loop.ctx.turn += 1
        third, _ = next_action(loop)
    finally:
        failover.reset()
    assert first == second == third == VALID
    assert [schema is None for _, schema in endpoint.requests] == [
        False, False, False, True, False]
    assert all(messages[0] == prefix for messages, _ in endpoint.requests)
    assert [len(contract_notes(messages)) for messages, _ in endpoint.requests] == [
        0, 0, 0, 1, 1]
    assert len(contract_notes(loop.messages)) == 1


def test_mid_turn_failover_to_a_plain_endpoint_gets_the_contract(make_routine):
    head = RecordingEndpoint([EndpointError("head: HTTP 503 overloaded")])
    tail = RecordingEndpoint([VALID], mode="none")
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="plain-contract"))
    prefix = prepare(loop)
    try:
        action, _ = next_action(loop)
    finally:
        failover.reset()
    assert action == VALID
    assert len(head.requests) == len(tail.requests) == 1
    assert not contract_notes(head.requests[0][0])
    assert tail.requests[0][0][0] == prefix
    assert len(contract_notes(tail.requests[0][0])) == 1, (
        "the fallback's first request needs the contract even inside the same turn")
    assert len(contract_notes(loop.messages)) == 1
