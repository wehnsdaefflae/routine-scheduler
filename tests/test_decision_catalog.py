"""What survived the `decide` ACTION: the models a run is told it reaches, and old records. D288
retired `llm` and `decide` as kinds — a model is reached through a util capability now — so the
action's own contract left this repo with its handler. Two things did not: the CAPABILITIES line
naming this run's DECISION models (`engine/decisioncatalog.py`), gated on `util` rather than on
a kind, and the rendering of a RECORDED `decide` observation (`engine/obs_retired.py`), which a
resumed leg, past-run search and the console still need for the life of the instance."""

from __future__ import annotations

import yaml

from conftest import finish, write_file
from helpers import server_for
from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig
from rsched.engine.obs_retired import format_retired
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine

TS = "20261007-120000"


def _with_decision_models(server):
    server.decision_endpoints = {
        "predator": DecisionEndpointConfig(name="predator", protocol="systemone",
                                           base_url="http://box:8790/v1"),
        "jev": DecisionEndpointConfig(name="jev", protocol="systemone")}
    server.decision_models = {
        "vl8b": DecisionModelConfig(name="vl8b", endpoint="predator", model="qwen3-vl",
                                    multimodal=True),
        "jev": DecisionModelConfig(name="jev", endpoint="jev", model="jev-latest")}
    server.decision_model, server.decision_media_model = "jev", "vl8b"
    return server


# --- 1. the models a run is told it reaches ------------------------------------------------

def test_the_prompt_names_the_decision_models_without_naming_a_kind(make_routine, scripted):
    """The line says WHICH models answer and what each takes — and no longer spells a call. It names
    no util either: the standing order is that a contract names the CAPABILITY, so the line survives
    a library that changes under it."""
    d = make_routine(slug="named")
    ep = scripted([write_file("state/probe.txt"), finish()])
    run_routine(d, _with_decision_models(server_for(d)), run_ts=TS)
    system = ep.calls[0]["messages"][0]["content"]
    assert ("Decision models: jev (text, default), vl8b (text + images, default for images)"
            in system)
    assert "- decide:" not in system, "the retired kind must not have a prose bullet"
    assert "decide" not in ep.calls[0]["schema"]["properties"]["kind"]["enum"]


def test_a_routines_own_decision_role_is_the_one_it_is_told_about(make_routine, scripted):
    """A routine binding its own decision role reads ITS default, not the instance's."""
    d = make_routine(slug="roled")
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    raw["models"] = {"decision": "vl8b"}
    (d / "routine.yaml").write_text(yaml.safe_dump(raw))
    ep = scripted([write_file("state/probe.txt"), finish()])
    run_routine(d, _with_decision_models(server_for(d)), run_ts=TS)
    assert ("Decision models: jev (text), vl8b (text + images, default · default for images)"
            in ep.calls[0]["messages"][0]["content"])


def test_an_instance_with_no_decision_model_says_nothing_about_them(make_routine, scripted):
    """"" is the whole behaviour: a run is never told about a capability it cannot reach."""
    d = make_routine(slug="plain")
    ep = scripted([write_file("state/probe.txt"), finish()])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert "Decision models" not in ep.calls[0]["messages"][0]["content"]


# --- 2. a RECORDED decide observation still reads the way it did live ----------------------

def test_a_recorded_observation_reads_as_numbers():
    """The exact words the deleted handler's renderer produced, byte for byte. `engine/samestub.py`'s
    contract is that a replay says what the live turn said, so this wording is pinned rather than
    merely non-empty — a transcript that renders differently after an upgrade reads as another run."""
    text = format_retired({"kind": "decide", "model": "vl8b",
                           "files": [{"path": "a.png", "as": "image"}],
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


def test_a_recorded_failure_reads_as_the_failure_it_was():
    """Through the real chain, which is where floor 1 would otherwise re-word it."""
    assert format_observation({"kind": "decide", "error": "no key"}) == \
        "OBSERVATION (decide FAILED): no key"
    assert format_observation({"kind": "llm", "error": "no key"}) == \
        "OBSERVATION (llm subcall FAILED): no key"
    assert format_observation({"kind": "llm", "reply": "the answer"}) == \
        "OBSERVATION (llm reply):\nthe answer"


def test_a_live_kind_is_not_touched_by_the_retired_renderer():
    """It must return None for everything else, or it would swallow the chain behind it."""
    assert format_retired({"kind": "util", "error": "x"}, "util") is None
    assert format_retired({"kind": "list_models"}, "list_models") is None
