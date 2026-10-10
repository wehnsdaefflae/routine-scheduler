"""Past actions go to an Anthropic model as CALLS of the action tool, their observations as the
calls' results (anthropic_messages.native_turns) — so a thinking model's reply ends at its one
call instead of writing an action as text and carrying on (R2443-R2445)."""

import json

import rsched.endpoints.anthropic_api as anth_mod
from rsched.config import EndpointConfig
from rsched.endpoints.anthropic_api import AnthropicEndpoint
from rsched.endpoints.anthropic_messages import native_turns

READ = {"say": "Reading the stage.", "kind": "read_file", "path": "stages/a.md"}
EDIT = {"say": "Adding the field.", "kind": "edit_file", "path": "m.py", "anchor": "a",
        "replacement": "b"}
HISTORY = [
    {"role": "user", "content": "Begin run x."},
    {"role": "assistant", "content": json.dumps(READ)},
    {"role": "user", "content": "OBSERVATION (read_file stages/a.md): body"},
    {"role": "assistant", "content": json.dumps(EDIT)},
    {"role": "user", "content": "OBSERVATION (edit_file m.py): 1 replacement"},
]


def test_each_action_is_a_call_and_the_next_message_its_result():
    out = native_turns(HISTORY, "action")
    assert out[0] == HISTORY[0]
    assert out[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "rs_00001", "name": "action", "input": READ}]}
    assert out[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "rs_00001", "content": HISTORY[2]["content"]}]}
    assert out[3]["content"][0]["input"] == EDIT
    assert out[4]["content"][0]["tool_use_id"] == out[3]["content"][0]["id"] == "rs_00003"


def test_a_message_renders_the_same_bytes_on_every_later_turn():
    """The caching contract: the rendering is positional, so growing the list never changes
    a message already sent — the cached prefix holds."""
    later = [*HISTORY, {"role": "assistant", "content": json.dumps(READ)},
             {"role": "user", "content": "OBSERVATION (read_file stages/a.md): body"}]
    short, long_ = native_turns(HISTORY, "action"), native_turns(later, "action")
    assert json.dumps(long_[:len(short)]) == json.dumps(short)


def test_what_is_not_one_json_object_stays_text_and_so_does_its_answer():
    debris = [{"role": "user", "content": "Begin."},
              {"role": "assistant", "content": '{"say": "cut off at 4000 chars", "kind": "wri'},
              {"role": "user", "content": "Your previous reply was not a valid action"}]
    assert native_turns(debris, "action") == debris


def test_media_rides_after_the_result_and_a_call_with_no_answer_stays_text():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "AA=="}}
    viewed = [{"role": "assistant", "content": json.dumps(READ)},
              {"role": "user", "content": [{"type": "text", "text": "OBSERVATION"}, image]}]
    out = native_turns(viewed, "action")
    assert out[1]["content"] == [{"type": "tool_result", "tool_use_id": "rs_00000",
                                  "content": [{"type": "text", "text": "OBSERVATION"}]}, image]
    dangling = HISTORY[:2]
    assert native_turns(dangling, "action") == dangling


class _Reply:
    status_code = 200
    text = ""
    headers: dict = {}  # noqa: RUF012 — a read-only stand-in for httpx.Response.headers

    @staticmethod
    def json():
        return {"content": [{"type": "tool_use", "name": "action", "input": READ}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}


def _sent(monkeypatch, endpoint, **kw) -> list:
    bodies: list = []

    def post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        return _Reply()
    monkeypatch.setattr(anth_mod.httpx, "post", post)
    endpoint.complete(HISTORY, model="claude-opus-5", **kw)
    return bodies[0]["messages"]


def test_the_adapter_sends_calls_on_auto_and_text_where_no_tool_or_a_forced_one(monkeypatch):
    """On `auto` with the tool offered — the path a thinking model double-emits on. A call
    with no schema offers no tool (the API refuses a tool_use without one), and a forced route
    keeps the text history it was validated on."""
    auto = AnthropicEndpoint(EndpointConfig(name="a", kind="anthropic", api_key="k"))
    sent = _sent(monkeypatch, auto, schema={"type": "object"})
    assert sent[1]["content"][0]["type"] == "tool_use"
    assert sent[2]["content"][0]["type"] == "tool_result"
    assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}   # tail marker
    plain = _sent(monkeypatch, auto)
    assert plain[1]["content"] == json.dumps(READ)
    forced = AnthropicEndpoint(EndpointConfig(name="f", kind="anthropic", api_key="k",
                                              tool_choice="forced"))
    assert _sent(monkeypatch, forced, schema={"type": "object"})[1]["content"] == json.dumps(READ)
