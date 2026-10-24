"""A reply that chains as several CALLS reaches the engine whole (F754).

Action chaining (0.446.0, engine/chain.py) runs every action one reply wrote. Two channels
carry them: text blocks, which `replyactions.written_actions` reads, and the action tool's
calls. The Anthropic adapter asks for at most one call (`_ONE_CALL_AT_MOST`) precisely so the
order text-then-call is unambiguous — but `disable_parallel_tool_use` is a REQUEST, and a wire
that ignores it sends several. Until this change `_parse` kept `calls[0]`, logged a warning
nobody reads and dropped the rest: `Completion.parsed` was one object, so they never reached
`reply_actions` at all and chaining could not run them. The run was never told — the extra
actions vanished with no `unexecuted_note`, so its narration claimed effects for actions
nobody ran, which is exactly the R2443-R2445 failure chaining exists to end, surviving on one
channel.

So `Completion` carries the calls it received — `parsed` the first, `parsed_rest` the ones
after it, in wire order — and `reply_actions` folds them in after the reply's text actions.
Nothing else reads `parsed_rest`: the chain is already the engine's one path for "more than
one action in a reply", and it stops at a failure, refuses a later `finish` and re-judges every
element exactly as it does for actions written as text.
"""

import json

import pytest

import rsched.endpoints.anthropic_api as anth_mod
from conftest import finish, write_file
from helpers import server_for
from rsched.config import EndpointConfig
from rsched.endpoints.anthropic_api import AnthropicEndpoint
from rsched.endpoints.base import Completion
from rsched.engine.replyactions import reply_actions
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]

EDIT = {"say": "Adding the two fields.", "kind": "edit_file", "path": "models.py",
        "anchor": "a", "replacement": "b"}
READ = {"say": "Fields added. Now reading the runner.", "kind": "read_file",
        "path": "runner.py"}
LIST = {"say": "And the directory it lives in.", "kind": "read_file", "path": "src/"}


class _Response:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers: dict = {}

    def json(self):
        return self._payload


def _calls_reply(*inputs, text=""):
    """A Messages API reply carrying `inputs` as that many calls of the action tool — the shape
    a wire produces when it ignores `disable_parallel_tool_use`."""
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks += [{"type": "tool_use", "name": "action", "input": obj} for obj in inputs]
    return {"content": blocks, "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 9}}


def _answering(monkeypatch, *payloads):
    it = iter(payloads)

    def fake_post(url, json=None, headers=None, timeout=None):
        return _Response(next(it))
    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)


def _endpoint(**cfg):
    return AnthropicEndpoint(EndpointConfig(
        name="anthropic", kind="anthropic", api_key="sk-test", context_tokens=800000, **cfg))


def test_two_calls_in_one_reply_both_reach_the_completion(monkeypatch):
    """The first is `parsed`, as every reader of it expects; the second rides `parsed_rest`."""
    _answering(monkeypatch, _calls_reply(EDIT, READ))
    c = _endpoint().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert c.parsed == EDIT
    assert c.parsed_rest == [READ]


def test_three_calls_keep_their_wire_order_and_a_lone_call_carries_nothing_extra(monkeypatch):
    _answering(monkeypatch, _calls_reply(EDIT, READ, LIST), _calls_reply(EDIT))
    ep = _endpoint()
    c = ep.complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert c.parsed == EDIT and c.parsed_rest == [READ, LIST]
    one = ep.complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert one.parsed == EDIT and one.parsed_rest == []


def test_reply_actions_folds_the_extra_calls_in_after_the_text_actions(monkeypatch):
    """Reply order is what the model meant: its text actions first, then its calls in wire
    order (engine/replyactions.py's docstring says why)."""
    _answering(monkeypatch, _calls_reply(READ, LIST, text=json.dumps(EDIT)))
    c = _endpoint().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert reply_actions(c) == [EDIT, READ, LIST]


def test_an_action_the_reply_wrote_twice_over_two_channels_is_still_one_action(monkeypatch):
    """The dedupe is per REPLY, not per channel: a model that writes a call out as text too
    must not have that action run twice, and must not be told it "did not run"."""
    _answering(monkeypatch, _calls_reply(EDIT, READ, text=json.dumps(EDIT)))
    c = _endpoint().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert reply_actions(c) == [EDIT, READ]


def test_a_stringly_first_call_is_still_unreadable_and_promotes_nothing(monkeypatch):
    """A wire that sends a string where the action object belongs is UNREADABLE — that was true
    before chaining and stays true: the adapter re-asks with the tool forced rather than
    promote a later call into first place, because the order is what the model meant. Pinned
    here because `parsed_rest` makes promoting look harmless; it is not.
    """
    stringly = _calls_reply(EDIT, text="")
    stringly["content"][0]["input"] = '{"kind": "finish"}'
    _answering(monkeypatch, stringly, _calls_reply(EDIT, READ))
    c = _endpoint().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert c.parsed == EDIT and c.parsed_rest == [READ]      # the FORCED re-ask's answer
    assert c.stop_details.get("forced_reask") == "tool_use 'action' (input: str)"


def test_the_forced_reask_keeps_the_calls_the_second_reply_carried(monkeypatch):
    """An unreadable `auto` reply is re-asked with the tool forced; whatever that answer
    carries is what the engine must run, extra calls included."""
    unreadable = {"content": [{"type": "thinking", "thinking": "", "signature": "s"}],
                  "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 40}}
    _answering(monkeypatch, unreadable, _calls_reply(EDIT, READ))
    c = _endpoint().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert c.parsed == EDIT and c.parsed_rest == [READ]
    assert c.stop_details.get("forced_reask") == "thinking"


def test_a_reply_that_chains_as_calls_runs_every_action(make_routine, scripted):
    """End to end, through the engine the adapter feeds: three calls in one reply write three
    files, inside ONE turn, and the run is told nothing was left unexecuted."""
    d = make_routine(slug="callchain")
    three = Completion(text="",
                       parsed=write_file("state/a.txt", content="a", say="First."),
                       parsed_rest=[write_file("state/b.txt", content="b", say="Second."),
                                    write_file("state/c.txt", content="c", say="Third.")],
                       usage={"in": 1, "out": 1})
    ep = scripted([three, finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261024-140000")
    assert status == "ok"
    assert [(d / "state" / f"{n}.txt").read_text() for n in "abc"] == ["a", "b", "c"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    acted = [(e["turn"], e["payload"]["kind"]) for e in events if e["type"] == "assistant_action"]
    assert acted[:3] == [(1, "write_file"), (1, "write_file"), (1, "write_file")]
    # Nothing was left over, so no observation names an unexecuted action — the thing F754 could
    # not do: the dropped calls had no `not_executed` row, so the run was never told at all.
    observed = [e["payload"] for e in events if e["type"] == "observation"]
    assert all("not_executed" not in p for p in observed)
    assert len(ep.calls) == 2                       # one model call for the whole chain


@pytest.mark.parametrize("field_name", ["parsed", "parsed_rest"])
def test_the_completion_names_both_channels(field_name):
    """A guard against the regression that caused F754: a Completion whose extra calls have
    nowhere to live drops them at the wire, where no chain can see them."""
    assert field_name in Completion.__dataclass_fields__
