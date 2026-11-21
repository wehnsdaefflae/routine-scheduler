"""An alias kind lands as the util call it spells, and its `assistant_action` EVENT says how it was
spelled (`spelled`, engine/kindaliases.py) — never the payload, never the model's messages."""
from __future__ import annotations

import json

from conftest import finish, util
from helpers import server_for
from rsched import utils_lib, utils_run
from rsched.endpoints.base import Completion
from rsched.engine.history import replay_messages
from rsched.engine.kindaliases import Spelled, event_mark, spelled_kind
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events


def _reply(*actions):
    text = "\n".join(json.dumps(a) for a in actions[:-1])
    return Completion(text=text, parsed=actions[-1], usage={"in": 1, "out": 1})


def _run(make_routine, scripted, monkeypatch, replies, slug):
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "list_utils", lambda home: [])
    monkeypatch.setattr(utils_run, "run_util", lambda home, name, args, **_kw: (0, "hit", ""))
    d = make_routine(slug=slug)
    scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts="20261121-090000")
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    return [e for e in events if e["type"] == "assistant_action"]


def test_each_element_of_a_chain_carries_its_own_spelling(make_routine, scripted, monkeypatch):
    """A chain of an alias, a plain util call and the hyphenated alias: three util events, and
    only the two aliases marked — each with its OWN kind, so no single slot could have served."""
    landed = _run(make_routine, scripted, monkeypatch, [
        _reply({"say": "s", "kind": "websearch", "args": ["three terms"]},
               util("page-fetch", ["https://example.org"]),
               {"say": "s", "kind": "code-search", "args": ["needle", "src/"]}),
        finish()], "spelled1")
    assert [e["payload"]["kind"] for e in landed] == ["util", "util", "util", "finish"]
    assert [e["payload"].get("name") for e in landed[:3]] == ["websearch", "page-fetch",
                                                              "code-search"]
    assert [e.get("spelled") for e in landed] == ["websearch", None, "code_search", None]
    assert all("spelled" not in e["payload"] for e in landed)


def test_the_same_call_written_as_a_util_lands_unmarked(make_routine, scripted, monkeypatch):
    """Two turns making one call, spelled two ways: identical payloads, one mark."""
    landed = _run(make_routine, scripted, monkeypatch, [
        util("websearch", ["q"], say="s"), {"say": "s", "kind": "websearch", "query": "q"},
        finish()], "spelled2")
    assert "spelled" not in landed[0]
    assert landed[1]["spelled"] == "websearch"
    assert landed[0]["payload"] == landed[1]["payload"]


def test_a_replayed_leg_hands_back_the_util_call_alone(make_routine, scripted, monkeypatch):
    d = make_routine(slug="spelled3")
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "list_utils", lambda home: [])
    monkeypatch.setattr(utils_run, "run_util", lambda home, name, args, **_kw: (0, "hit", ""))
    scripted([{"say": "s", "kind": "websearch", "args": ["q"]}, finish()])
    _status, run_dir = run_routine(d, server_for(d), run_ts="20261121-090100")
    messages, _turn, _records = replay_messages(read_events(run_dir / "transcript.jsonl")[0])
    first = json.loads(next(m["content"] for m in messages if m["role"] == "assistant"))
    assert first["kind"] == "util" and "spelled" not in first


def test_the_mark_names_the_kind_and_declines_what_is_not_rewritten():
    assert spelled_kind({"kind": "code-search", "args": ["x"]}) == "code_search"
    assert spelled_kind({"kind": "websearch", "args": ["x"]}) == "websearch"
    assert spelled_kind({"kind": "websearch"}) is None          # no payload: not rewritten
    assert spelled_kind({"kind": "util", "name": "websearch", "args": ["x"]}) is None
    marked = Spelled({"kind": "util", "name": "websearch"}, "websearch")
    assert marked == {"kind": "util", "name": "websearch"}      # a plain dict to every reader
    assert json.loads(json.dumps(marked)) == dict(marked)
    assert event_mark(marked) == {"spelled": "websearch"}
    assert event_mark(dict(marked)) == {}
