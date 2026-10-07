"""The predator decision server's protocol half (deploy/decision-server/decision_protocol.py).

The server itself needs llama.cpp and a GPU box; this half needs neither, so the wire format it
accepts and the answers it shapes are pinned here — the same shape `endpoints/decisions_openai.py`
sends and reads, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "deploy" / "decision-server" / "decision_protocol.py"
_spec = importlib.util.spec_from_file_location("decision_protocol", _PATH)
assert _spec and _spec.loader
proto = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("decision_protocol", proto)   # dataclasses resolve their module
_spec.loader.exec_module(proto)

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


def _parse(body):
    return proto.parse_request(body, max_images=4, max_questions=8)


def test_parses_the_published_shape_with_several_images():
    req = _parse({"input": [{"role": "user", "content": [
        {"type": "input_text", "text": "two photos"},
        {"type": "input_image", "image_url": PNG}, {"type": "input_image", "image_url": PNG}]}],
        "questions": [
            {"type": "predicate", "name": "p", "instructions": "Is it red?"},
            {"type": "choice", "name": "c", "instructions": "Which?",
             "choices": [{"value": "a", "description": "A"}, {"value": "b"}]},
            {"type": "score", "name": "s", "instructions": "How much?",
             "levels": [{"label": "low"}, {"label": "high", "description": "a lot"}]}]})
    assert req.evidence == "two photos"
    assert req.images == [b"\x89PNG fake", b"\x89PNG fake"]
    assert [o.description for o in req.questions[1].options] == ["A", "b"]
    assert [o.description for o in req.questions[2].options] == ["low", "high: a lot"]
    assert [o.value for o in req.questions[0].options] == ["true", "false"]


def test_a_string_input_is_the_evidence_and_unnamed_questions_get_names():
    req = _parse({"input": "charged twice", "questions": [
        {"type": "predicate", "instructions": "Billing?"}]})
    assert (req.evidence, req.images, req.questions[0].name) == ("charged twice", [],
                                                                 "question_0")


@pytest.mark.parametrize(("body", "param"), [
    ({"input": ["x"], "questions": []}, "input[0].role"),
    ({"input": [{"role": "assistant", "content": "x"}], "questions": []}, "input[0].role"),
    ({"input": "", "questions": []}, "questions"),
    ({"input": "x", "questions": [{"type": "maybe", "instructions": "?"}]}, "questions[0].type"),
    ({"input": "x", "questions": [{"type": "choice", "instructions": "?",
                                   "choices": [{"value": "only"}]}]}, "questions[0].choices"),
    ({"input": "x", "questions": [{"type": "choice", "instructions": "?",
                                   "choices": [{"value": "a"}, {"value": "a"}]}]},
     "questions[0].choices"),
    ({"input": "x", "questions": [{"type": "predicate", "name": "a", "instructions": "?"},
                                  {"type": "predicate", "name": "a", "instructions": "?"}]},
     "questions"),
    ({"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "https://example.com/x.png"}]}],
      "questions": [{"type": "predicate", "instructions": "?"}]},
     "input[0].content[0].image_url"),
    ({"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,***"}]}],
      "questions": [{"type": "predicate", "instructions": "?"}]},
     "input[0].content[0].image_url"),
])
def test_rejections_name_the_field(body, param):
    with pytest.raises(proto.BadRequestError) as err:
        _parse(body)
    assert err.value.param == param


def test_the_image_cap_is_enforced():
    content = [{"type": "input_image", "image_url": PNG}] * 5
    with pytest.raises(proto.BadRequestError, match="at most 4 images"):
        _parse({"input": [{"role": "user", "content": content}],
                "questions": [{"type": "predicate", "instructions": "?"}]})


def test_prompt_is_semifs_with_evidence_leading():
    q = _parse({"input": "x", "questions": [{"type": "choice", "instructions": "Which?",
                                             "choices": [{"value": "a"}, {"value": "b"}]}]}
               ).questions[0]
    text = proto.payload_text("", 2, q)
    assert text.startswith('{"evidence": "See the attached images."')
    assert [o["letter"] for o in json.loads(text)["options"]] == ["A", "B"]
    assert proto.system_for(0) == proto.SYSTEM_TEXT
    assert proto.system_for(1) == proto.SYSTEM_IMAGE
    assert proto.system_for(3) == proto.SYSTEM_IMAGES


def test_answers_take_the_published_shape():
    req = _parse({"input": "x", "questions": [
        {"type": "predicate", "name": "p", "instructions": "?"},
        {"type": "choice", "name": "c", "instructions": "?",
         "choices": [{"value": "a"}, {"value": "b"}]},
        {"type": "score", "name": "s", "instructions": "?",
         "levels": [{"label": "low"}, {"label": "high"}]}]})
    p, c, s = (proto.answer(q, lg) for q, lg in zip(req.questions,
                                                    ([2.0, 0.0], [0.0, 3.0], [0.0, 0.0]),
                                                    strict=True))
    assert p["type"] == "predicate"
    assert p["probability"] > 0.88
    assert c["choice"] == "b"
    assert c["probabilities"][1] == {"value": "b", "probability": c["confidence"]}
    assert s["score"] == pytest.approx(0.5)
    assert s["probabilities"][1]["label"] == "high"


def test_softmax_refuses_non_finite_scores():
    with pytest.raises(ValueError, match="finite"):
        proto.softmax([float("nan"), 1.0])
