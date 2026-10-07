"""Decision-model transports (endpoints/decisions*.py) and their config (config/decisionconf.py).

The two wire protocols are pinned from their published shapes — OpenAI's Decisions guide and
TypeSafe's System One reference / OpenRouter's Jev tutorial — with the HTTP call replaced, so the
body each adapter sends and the answers it reads back are checked without a network.
"""

from __future__ import annotations

import httpx
import pytest
import yaml

from rsched.config import ServerConfig, load_server_config
from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig
from rsched.endpoints import decisions_openai, decisions_systemone, instrument
from rsched.endpoints.base import EndpointError
from rsched.endpoints.decisions import (
    DecisionImage,
    DecisionOption,
    DecisionQuestion,
    decision_catalog,
    pick_decision_model,
)

QS = [DecisionQuestion("is_bug", "yes_no", "Is it a defect?"),
      DecisionQuestion("team", "choice", "Which team?",
                       [DecisionOption("payments", "Checkout issues"), DecisionOption("frontend")]),
      DecisionQuestion("urgency", "score", "How urgent?",
                       [DecisionOption("low", "can wait"), DecisionOption("high")])]


def _server(**over) -> ServerConfig:
    s = ServerConfig(
        decision_endpoints={
            "luna": DecisionEndpointConfig(name="luna", protocol="openai"),
            "predator": DecisionEndpointConfig(name="predator", protocol="openai",
                                               base_url="http://box:8790/v1", api_key="tok"),
            "jev": DecisionEndpointConfig(name="jev", protocol="systemone",
                                          base_url="https://openrouter.ai/api/v1",
                                          key_var="OPENROUTER_API_KEY")},
        decision_models={
            "jev": DecisionModelConfig(name="jev", endpoint="jev", model="typesafe/jev-1.13"),
            "vl8b": DecisionModelConfig(name="vl8b", endpoint="predator",
                                        model="qwen3-vl-8b-instruct"),
            "luna-text": DecisionModelConfig(name="luna-text", endpoint="luna",
                                             model="gpt-6-luna", multimodal=False)},
        decision_model="jev", decision_media_model="vl8b")
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _respond(monkeypatch, module, body: dict, status: int = 200, seen: list | None = None):
    def fake(url, payload, headers, timeout, *, name):
        if seen is not None:
            seen.append({"url": url, "body": payload, "headers": headers, "timeout": timeout})
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(module, "post_json", fake)


# --- config ------------------------------------------------------------------------------------
def _load(tmp_path, extra: dict):
    cfg = {"endpoints": {"d": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
           "models": {"m": {"endpoint": "d", "model": "m"}}, "system_model": "m", **extra}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return load_server_config(tmp_path / "config.yaml")


def test_the_decision_catalog_loads_with_per_protocol_key_defaults(tmp_path):
    server, problems = _load(tmp_path, {
        "decision_endpoints": {"oa": {"protocol": "openai"}, "ts": {"protocol": "systemone"}},
        "decision_models": {"a": {"endpoint": "oa", "model": "gpt-6-luna"},
                            "b": {"endpoint": "ts", "model": "jev-latest"}},
        "decision_model": "b", "decision_media_model": "a"})
    assert problems == []
    assert server.decision_endpoints["oa"].key_var == "OPENAI_API_KEY"
    assert server.decision_endpoints["ts"].key_var == "TYPESAFE_API_KEY"
    assert server.decision_models["a"].name == "a"


def test_catalog_mistakes_are_reported(tmp_path):
    _, problems = _load(tmp_path, {
        "decision_endpoints": {"ts": {"protocol": "systemone", "colour": "red"}},
        "decision_models": {"x": {"endpoint": "nope", "model": "m"},
                            "y": {"endpoint": "ts", "model": "jev", "multimodal": True}},
        "decision_model": "ghost", "decision_media_model": "y"})
    text = "\n".join(problems)
    assert "decision_endpoints.ts.colour: unknown key" in text
    assert "decision_models.x: decision endpoint 'nope' is not configured" in text
    assert "decision_models.y: multimodal is on, but the systemone protocol" in text
    assert "decision_model: 'ghost' is not a decision model" in text
    assert "decision_media_model: 'y' does not take images" in text


# --- picking a model ---------------------------------------------------------------------------
def test_defaults_follow_the_payload():
    s = _server()
    assert pick_decision_model(s, None, images=False)[1].name == "jev"
    assert pick_decision_model(s, None, images=True)[1].name == "vl8b"
    assert pick_decision_model(s, "luna-text", images=False)[1].model == "gpt-6-luna"


def test_a_bad_pick_names_the_alternatives():
    s = _server()
    with pytest.raises(EndpointError, match="Decision models: jev, luna-text, vl8b"):
        pick_decision_model(s, "gpt-4o", images=False)
    with pytest.raises(EndpointError, match="Models that take images: vl8b"):
        pick_decision_model(s, "jev", images=True)
    with pytest.raises(EndpointError, match="no default decision model"):
        pick_decision_model(_server(decision_model="", decision_media_model=""), None,
                            images=False)


def test_catalog_rows_mark_the_defaults():
    rows = {r["name"]: r for r in decision_catalog(_server())}
    assert rows["jev"] == {"name": "jev", "endpoint": "jev", "model": "typesafe/jev-1.13",
                           "protocol": "systemone", "images": False, "default": "default"}
    assert rows["vl8b"]["images"] is True
    assert rows["vl8b"]["default"] == "default for images"
    assert rows["luna-text"]["images"] is False   # the model's own flag beats the protocol's


# --- the openai protocol -----------------------------------------------------------------------
def test_openai_sends_the_published_shape(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    seen: list = []
    _respond(monkeypatch, decisions_openai, {"answers": [], "usage": {}}, seen=seen)
    endpoint, ref = pick_decision_model(_server(), "vl8b", images=True)
    with pytest.raises(EndpointError, match="no answer came back for is_bug, team, urgency"):
        endpoint.decide("two photos", [DecisionImage(img, "image/png")], QS, model=ref.model,
                        timeout=30)
    call = seen[0]
    assert call["url"] == "http://box:8790/v1/decisions"
    assert call["headers"] == {"Authorization": "Bearer tok"}
    assert call["body"]["input"] == [{"role": "user", "content": [
        {"type": "input_text", "text": "two photos"},
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw=="}]}]
    assert call["body"]["questions"] == [
        {"type": "predicate", "name": "is_bug", "instructions": "Is it a defect?"},
        {"type": "choice", "name": "team", "instructions": "Which team?",
         "choices": [{"value": "payments", "description": "Checkout issues"},
                     {"value": "frontend"}]},
        {"type": "score", "name": "urgency", "instructions": "How urgent?",
         "levels": [{"label": "low", "description": "can wait"}, {"label": "high"}]}]
    text_only = endpoint.body({"ticket": "x"}, [], QS[:1], "m")
    assert text_only["input"] == '{\n "ticket": "x"\n}'


def test_openai_answers_are_normalised(monkeypatch):
    _respond(monkeypatch, decisions_openai, {"model": "qwen3-vl", "answers": [
        {"type": "score", "name": "urgency", "score": 0.8, "confidence": 0.6,
         "probabilities": [{"value": 0, "label": "low", "probability": 0.2},
                           {"value": 1, "label": "high", "probability": 0.8}]},
        {"type": "predicate", "name": "is_bug", "probability": 0.92},
        {"type": "refusal", "name": "team"}],
        "usage": {"input_tokens": 480, "output_tokens": 0}})
    endpoint, ref = pick_decision_model(_server(), "vl8b", images=False)
    out = endpoint.decide("x", [], QS, model=ref.model, timeout=30)
    bug, team, urgency = out.answers
    assert (bug.probability, bug.probabilities) == (0.92, {"yes": 0.92, "no": 0.08})
    assert team.refused
    assert urgency.score == 0.8
    assert urgency.probabilities == {"low": 0.2, "high": 0.8}
    assert out.usage == {"in": 480, "out": 0}
    assert out.model == "qwen3-vl"


def test_a_malformed_answer_is_a_transport_error(monkeypatch):
    _respond(monkeypatch, decisions_openai, {"answers": [{"type": "choice", "name": "team"}]})
    endpoint, ref = pick_decision_model(_server(), "vl8b", images=False)
    with pytest.raises(EndpointError, match="does not have the choice shape"):
        endpoint.decide("x", [], [QS[1]], model=ref.model, timeout=30)


# --- the systemone protocol --------------------------------------------------------------------
def test_systemone_sends_and_reads_jevs_shape(monkeypatch):
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"OPENROUTER_API_KEY": "or-key"})
    seen: list = []
    _respond(monkeypatch, decisions_systemone, {
        "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
        "answers": {"is_bug": {"type": "noul", "noul": 0.96},
                    "team": {"type": "choice", "choice": "payments", "confidence": 0.67,
                             "probabilities": {"payments": 0.78, "frontend": 0.22}},
                    "urgency": {"type": "score", "score": 0.99, "confidence": 0.99,
                                "probabilities": {"0": 0.01, "1": 0.99},
                                "legend": {"0": "low: can wait", "1": "high"}}},
        "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992}}, seen=seen)
    endpoint, ref = pick_decision_model(_server(), None, images=False)
    out = endpoint.decide({"ticket": "blank page"}, [], QS, model=ref.model, timeout=30)
    call = seen[0]
    assert call["url"] == "https://openrouter.ai/api/v1/systemone"
    assert call["headers"] == {"Authorization": "Bearer or-key"}
    assert call["body"] == {"model": "typesafe/jev-1.13", "state": {"ticket": "blank page"},
                            "questions": {
        "is_bug": {"type": "noul", "instructions": "Is it a defect?"},
        "team": {"type": "choice", "instructions": "Which team?",
                 "criteria": {"payments": "Checkout issues", "frontend": "frontend"}},
        "urgency": {"type": "score", "instructions": "How urgent?",
                    "criteria": ["low: can wait", "high"]}}}
    assert out.answers[0].probability == 0.96
    assert out.answers[1].choice == "payments"
    assert out.answers[1].confidence == 0.67
    assert out.answers[2].probabilities == {"low": 0.01, "high": 0.99}
    assert out.usage == {"in": 476, "out": 70, "cost": 0.000019992}
    assert (out.model, out.provider) == ("typesafe/jev-1.13-20260917", "TypeSafe")


def test_systemone_refuses_images_and_needs_a_key(monkeypatch, tmp_path):
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    endpoint, ref = pick_decision_model(_server(), "jev", images=False)
    with pytest.raises(EndpointError, match="takes text only"):
        endpoint.decide("x", [DecisionImage(tmp_path / "a.png", "image/png")], QS,
                        model=ref.model, timeout=30)
    with pytest.raises(EndpointError, match="no API key") as err:
        endpoint.decide("x", [], QS, model=ref.model, timeout=30)
    assert err.value.auth


def test_an_auth_failure_says_so(monkeypatch):
    _respond(monkeypatch, decisions_openai, {"error": {"message": "bad key"}}, status=401)
    endpoint, ref = pick_decision_model(_server(), "vl8b", images=False)
    with pytest.raises(EndpointError) as err:
        endpoint.decide("x", [], QS, model=ref.model, timeout=30)
    assert err.value.auth


# --- instrumentation ---------------------------------------------------------------------------
def test_a_decision_is_recorded_like_a_completion(monkeypatch):
    records: list = []

    class Sink:
        def record(self, rec):
            records.append(rec)

    instrument.set_sink(Sink())
    try:
        _respond(monkeypatch, decisions_openai, {"answers": [
            {"type": "predicate", "name": "is_bug", "probability": 0.5}],
            "usage": {"input_tokens": 9}})
        from rsched.endpoints.decisions import decide
        endpoint, ref = pick_decision_model(_server(), "vl8b", images=False)
        decide(endpoint, ref, "x", [], QS[:1], timeout=30, purpose="decide · triage")
    finally:
        instrument.set_sink(None)
    assert [r["phase"] for r in records] == ["started", "finished"]
    assert records[1]["usage"] == {"in": 9, "out": 0}
    assert (records[0]["kind"], records[0]["endpoint"]) == ("decide", "predator")
