"""The `decide` action (engine/decideaction.py): its contract, its evidence, its observation,
and the one rule that keeps it off an instance with no decision model.

The provider call is replaced at the adapter (`OpenAIDecisions.decide`), so a whole scripted run
exercises the real path — validation, routing, file gathering, usage, rendering — without a
network.
"""

from __future__ import annotations

import pytest

from conftest import finish, write_file
from helpers import server_for
from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig
from rsched.endpoints import decisions_openai
from rsched.endpoints.decisions import Decision, DecisionAnswer
from rsched.engine.actions import normalize_action, validate_action
from rsched.engine.decideaction import format_decide, questions_from
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261007-120000"


def _decide(**fields):
    return {"say": "Routing the ticket.", "kind": "decide", **fields}


@pytest.mark.parametrize("action", [
    _decide(question="Which queue?", options=["access: account access", "billing"],
            evidence="cannot log in"),
    _decide(question="Is it urgent?", evidence={"ticket": "down for 3 days"}),
    _decide(question="How severe?", answer_type="score", options=["low", "mid", "high"],
            files=["state/ticket.md"]),
    _decide(questions=[{"name": "is_bug", "question": "A defect?"},
                       {"name": "team", "question": "Which team?", "options": ["a", "b"]}],
            evidence="x", model="jev", background=True),
])
def test_valid_decide_actions(action):
    assert validate_action(normalize_action(action)) == []


@pytest.mark.parametrize(("action", "fragment"), [
    (_decide(evidence="x"), "needs 'question'"),
    (_decide(question="q"), "needs evidence"),
    (_decide(question="q", options=["only"], evidence="x"), "2-26 'options'"),
    (_decide(question="q", answer_type="yes_no", options=["a", "b"], evidence="x"),
     "takes no 'options'"),
    (_decide(question="q", answer_type="maybe", evidence="x"), "'answer_type' must be one of"),
    (_decide(question="q", options=["a: x", "a: y"], evidence="x"), "share a value"),
    (_decide(question="q", questions=[{"name": "a", "question": "q"}], evidence="x"),
     "not both"),
    (_decide(questions=[{"name": "Bad Name", "question": "q"}], evidence="x"), "snake_case"),
    (_decide(questions=[{"name": "a", "question": "q"}, {"name": "a", "question": "r"}],
             evidence="x"), "names must be unique"),
    ({"say": "s", "kind": "ask_user", "question": "q", "options": list("abcdef")}, "at most 5"),
])
def test_decide_mistakes_are_taught(action, fragment):
    problems = validate_action(normalize_action(action))
    assert any(fragment in p for p in problems), problems


def test_options_split_into_value_and_meaning():
    (q,), _ = questions_from(_decide(question="Which?", options=[
        "billing: Payments, refunds", "a sentence with no value part", "technical:"]))
    assert [(o.value, o.description) for o in q.options] == [
        ("billing", "Payments, refunds"), ("a sentence with no value part", ""),
        ("technical:", "")]
    assert q.name == "answer"
    assert q.type == "choice"


def test_the_observation_reads_as_numbers():
    text = format_decide({"kind": "decide", "model": "vl8b", "files": [{"path": "a.png",
                                                                        "as": "image"}],
                          "answers": [
        {"name": "is_bug", "type": "yes_no", "probability": 0.962,
         "probabilities": {"yes": 0.962, "no": 0.038}},
        {"name": "team", "type": "choice", "choice": "payments", "confidence": 0.67,
         "probabilities": {"payments": 0.78, "frontend": 0.22}},
        {"name": "urgency", "type": "score", "score": 1.99, "confidence": 0.99,
         "probabilities": {"later": 0.0, "this week": 0.01, "now": 0.99}},
        {"name": "nsfw", "type": "yes_no", "refused": True, "probabilities": {}}]}, "decide")
    assert text == (
        "OBSERVATION (decide · vl8b over 1 image):\n"
        "- is_bug: P(yes) = 0.962\n"
        "- team: payments, confidence 0.67 — payments 0.780 · frontend 0.220\n"
        "- urgency: score 1.99 on 0..2, confidence 0.99 — [0] later 0.000 · [1] this week "
        "0.010 · [2] now 0.990\n"
        "- nsfw: REFUSED by the model — no answer to this question")
    assert format_observation({"kind": "decide", "error": "no key"}) == \
        "OBSERVATION (decide FAILED): no key"


# --- a whole run ---------------------------------------------------------------------------------
def _with_decision_models(server):
    server.decision_endpoints = {
        "predator": DecisionEndpointConfig(name="predator", protocol="openai",
                                           base_url="http://box:8790/v1"),
        "jev": DecisionEndpointConfig(name="jev", protocol="systemone")}
    server.decision_models = {
        "vl8b": DecisionModelConfig(name="vl8b", endpoint="predator", model="qwen3-vl"),
        "jev": DecisionModelConfig(name="jev", endpoint="jev", model="jev-latest")}
    server.decision_model, server.decision_media_model = "jev", "vl8b"
    return server


def test_a_run_decides_over_images_and_text_files(make_routine, scripted, monkeypatch):
    d = make_routine(slug="screen")
    (d / "state" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (d / "state" / "profile.md").write_text("Tall, likes hiking.", encoding="utf-8")
    calls: list = []

    def fake_decide(self, evidence, images, questions, *, model, timeout):
        calls.append({"endpoint": self.name, "evidence": evidence,
                      "images": [i.path.name for i in images],
                      "questions": [q.name for q in questions], "model": model,
                      "timeout": timeout})
        return Decision([DecisionAnswer("full_body", "yes_no", {"yes": 0.9, "no": 0.1},
                                        probability=0.9),
                         DecisionAnswer("mood", "choice", {"calm": 0.3, "bright": 0.7},
                                        choice="bright", confidence=0.7)],
                        usage={"in": 640, "out": 0})

    monkeypatch.setattr(decisions_openai.OpenAIDecisions, "decide", fake_decide)
    scripted([
        _decide(questions=[{"name": "full_body", "question": "Is the whole body visible?"},
                           {"name": "mood", "question": "Which mood?",
                            "options": ["calm: muted", "bright: vivid"]}],
                evidence="One profile.", files=["state/photo.png", "state/profile.md"]),
        finish(summary="screened"),
    ])
    status, run_dir = run_routine(d, _with_decision_models(server_for(d)), run_ts=TS)
    assert status == "ok"
    assert calls == [{"endpoint": "predator",
                      "evidence": {"evidence": "One profile.",
                                   "file state/profile.md": "Tall, likes hiking."},
                      "images": ["photo.png"], "questions": ["full_body", "mood"],
                      "model": "qwen3-vl", "timeout": 300}]
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = next(e["payload"] for e in events
               if e["type"] == "observation" and e["payload"].get("kind") == "decide")
    assert obs["model"] == "vl8b"
    assert obs["answers"][1]["choice"] == "bright"
    assert obs["usage"] == {"in": 640, "out": 0}
    assert obs["files"] == [{"path": "state/photo.png", "as": "image"},
                            {"path": "state/profile.md", "as": "text"}]


def test_a_text_only_model_is_never_sent_images(make_routine, scripted, monkeypatch):
    d = make_routine(slug="nopix")
    (d / "state" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(decisions_openai.OpenAIDecisions, "decide",
                        lambda *a, **k: pytest.fail("no request may be sent"))
    scripted([_decide(question="Smiling?", files=["state/photo.png"], model="jev"),
              finish()])
    _, run_dir = run_routine(d, _with_decision_models(server_for(d)), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = next(e["payload"] for e in events
               if e["type"] == "observation" and e["payload"].get("kind") == "decide")
    assert "takes text only" in obs["error"]
    assert "Models that take images: vl8b" in obs["error"]


def test_no_decision_model_means_no_decide_kind(make_routine, scripted):
    d = make_routine(slug="plain")
    ep = scripted([_decide(question="q?", evidence="x"), write_file("state/probe.txt"),
                   finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    first = ep.calls[0]
    assert "- decide:" not in first["messages"][0]["content"]
    assert "decide" not in first["schema"]["properties"]["kind"]["enum"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    errs = [e["payload"]["message"] for e in events if e["type"] == "error"]
    assert any("kind=decide is not available" in m for m in errs)


def test_the_prompt_names_the_decision_models(make_routine, scripted):
    d = make_routine(slug="named")
    ep = scripted([write_file("state/probe.txt"), finish()])
    run_routine(d, _with_decision_models(server_for(d)), run_ts=TS)
    system = ep.calls[0]["messages"][0]["content"]
    assert "- decide: put a typed question to a DECISION model" in system
    assert ("Decision models (decide): jev (text, default), vl8b (text + images, default for "
            "images)") in system
