"""The OpenAI Decisions wire format, and the SemIf readout it is answered with — no model here.

`decision_server.py` serves a local vision-language GGUF behind `POST /v1/decisions`, the
request/response shape OpenAI published for its Decisions API (developers.openai.com, "Decisions",
2026-10). This half is everything that needs no weights: parsing and validating a request,
building the prompt each question is scored against, and turning the logits read at the declared
answer slots into the typed answer the protocol returns. It is importable on any Python with
no third-party package at all, which is what lets the routine-scheduler suite check it
(tests/test_decision_server_protocol.py) without a GPU, Pillow or llama.cpp.

The prompt is SemIf's own (`semif_phase1.core.DIRECT_SYSTEM` + the JSON evidence/criterion/
lettered-options payload) and, with images, the wording `image-decide.py` was measured with on
predator (2026-10-04, 12/12 on ground truth) — so a decision served here is the decision that
was benchmarked, not a lookalike.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass, field

#: Answer slots: one uppercase letter per option, each verified to be ONE token at load. 26 is
#: the protocol's practical ceiling here — past it the readout would need multi-token slots.
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SYSTEM_TEXT = ("Apply the supplied criterion to the supplied evidence. Choose exactly one listed "
               "option. Respond with only its uppercase letter, with no explanation or reasoning.")
SYSTEM_IMAGE = ("Apply the supplied criterion to the supplied evidence, which includes the image. "
                "Choose exactly one listed option. Respond with only its uppercase letter, with "
                "no explanation or reasoning.")
SYSTEM_IMAGES = ("Apply the supplied criterion to ALL the supplied images together, as evidence "
                 "about the same subject. Choose exactly one listed option. Respond with only its "
                 "uppercase letter, with no explanation or reasoning.")
#: What a predicate is scored as: a choice between these two, P(first) being the answer.
PREDICATE_OPTIONS = (("true", "The answer is yes."), ("false", "The answer is no."))
QUESTION_TYPES = ("predicate", "choice", "score")


class BadRequestError(ValueError):
    """A request the protocol rejects; `param` names the offending field, as OpenAI's do."""

    def __init__(self, message: str, param: str = "") -> None:
        super().__init__(message)
        self.param = param


@dataclass
class Option:
    """One answer slot: what the answer reports (`value`) and what the model reads."""

    value: str | int
    description: str
    label: str = ""


@dataclass
class Question:
    """One question as scored: its echoed name, its type and its ordered answer slots."""

    name: str
    type: str
    instructions: str
    options: list[Option] = field(default_factory=list)


@dataclass
class DecisionRequest:
    """A parsed request: shared evidence (text + raw image bytes) and the questions asked of it."""

    evidence: str
    images: list[bytes]
    questions: list[Question]


def _text(value: object, param: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise BadRequestError(f"{param} must be a non-empty string", param)
    return value


def decode_data_url(url: object, param: str) -> bytes:
    """An inline `data:` URL's bytes — the only image form the protocol accepts."""
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        raise BadRequestError("images must be inline base64 data URLs (data:image/...;base64,...)",
                              param)
    head, _, data = url.partition(",")
    if ";base64" not in head:
        raise BadRequestError("image data URL must be base64-encoded", param)
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequestError(f"image data is not valid base64 ({exc})", param) from exc


def _input(raw: object, max_images: int) -> tuple[str, list[bytes]]:
    """`input` → (evidence text, images). A string, or user messages of text and image parts."""
    if isinstance(raw, str):
        return raw, []
    if not isinstance(raw, list) or not raw:
        raise BadRequestError("input must be a string or a non-empty list of user messages",
                              "input")
    texts: list[str] = []
    images: list[bytes] = []
    for i, msg in enumerate(raw):
        where = f"input[{i}]"
        if not isinstance(msg, dict) or msg.get("role", "user") != "user":
            raise BadRequestError("only user messages are supported", f"{where}.role")
        content = msg.get("content")
        parts = [{"type": "input_text", "text": content}] if isinstance(content, str) else content
        if not isinstance(parts, list):
            raise BadRequestError("content must be a string or a list of parts", f"{where}.content")
        for j, part in enumerate(parts):
            at = f"{where}.content[{j}]"
            kind = part.get("type") if isinstance(part, dict) else None
            if kind == "input_text":
                texts.append(_text(part.get("text"), f"{at}.text"))
            elif kind == "input_image":
                if len(images) >= max_images:
                    raise BadRequestError(f"at most {max_images} images per request", at)
                images.append(decode_data_url(part.get("image_url"), f"{at}.image_url"))
            else:
                raise BadRequestError("parts must be input_text or input_image", f"{at}.type")
    return "\n\n".join(texts), images


def _options(q: dict, kind: str, at: str) -> list[Option]:
    if kind == "predicate":
        return [Option(value, desc) for value, desc in PREDICATE_OPTIONS]
    key, name_key = ("choices", "value") if kind == "choice" else ("levels", "label")
    items = q.get(key)
    if not isinstance(items, list) or not 2 <= len(items) <= len(LETTERS):
        raise BadRequestError(f"{kind} needs 2-{len(LETTERS)} {key}", f"{at}.{key}")
    out: list[Option] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise BadRequestError(f"each of {key} must be an object", f"{at}.{key}[{i}]")
        name = item.get(name_key)
        if not isinstance(name, (str, int)) or isinstance(name, bool) or str(name) == "":
            raise BadRequestError(f"{name_key} must be a string or number",
                                  f"{at}.{key}[{i}].{name_key}")
        desc = _text(item.get("description"), f"{at}.{key}[{i}].description", required=False)
        text = f"{name}: {desc}" if desc and kind == "score" else (desc or str(name))
        out.append(Option(i, text, str(name)) if kind == "score" else Option(name, text))
    if kind == "choice" and len({str(o.value) for o in out}) != len(out):
        raise BadRequestError("choice values must be distinct", f"{at}.{key}")
    return out


def parse_request(body: object, *, max_images: int, max_questions: int) -> DecisionRequest:
    """Validate one `POST /v1/decisions` body against the published shape."""
    if not isinstance(body, dict):
        raise BadRequestError("body must be a JSON object")
    evidence, images = _input(body.get("input"), max_images)
    raw_qs = body.get("questions")
    if not isinstance(raw_qs, list) or not 1 <= len(raw_qs) <= max_questions:
        raise BadRequestError(f"questions must list 1-{max_questions} questions", "questions")
    questions: list[Question] = []
    for i, q in enumerate(raw_qs):
        at = f"questions[{i}]"
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES:
            raise BadRequestError(f"type must be one of {', '.join(QUESTION_TYPES)}", f"{at}.type")
        name = q.get("name") if q.get("name") is not None else f"question_{i}"
        questions.append(Question(_text(name, f"{at}.name"), q["type"],
                                  _text(q.get("instructions"), f"{at}.instructions"),
                                  _options(q, q["type"], at)))
    if len({q.name for q in questions}) != len(questions):
        raise BadRequestError("question names must be unique", "questions")
    return DecisionRequest(evidence, images, questions)


def system_for(n_images: int) -> str:
    """The system instruction for a context carrying `n_images` images."""
    return SYSTEM_TEXT if n_images == 0 else SYSTEM_IMAGE if n_images == 1 else SYSTEM_IMAGES


def payload_text(evidence: str, n_images: int, q: Question) -> str:
    """SemIf's user payload. Evidence leads, so questions over one input share a token prefix."""
    if not evidence:
        evidence = ("" if n_images == 0 else "See the attached image." if n_images == 1
                    else "See the attached images.")
    return json.dumps({"evidence": evidence, "criterion": q.instructions,
                       "options": [{"letter": LETTERS[i], "description": o.description}
                                   for i, o in enumerate(q.options)]}, ensure_ascii=False)


def softmax(values: list[float]) -> list[float]:
    """SemIf's softmax, including its refusal of non-finite scores."""
    if len(values) < 2 or any(not math.isfinite(v) for v in values):
        raise ValueError("Need at least two finite scores")
    top = max(values)
    weights = [math.exp(v - top) for v in values]
    total = sum(weights)
    return [w / total for w in weights]


def answer(q: Question, logits: list[float]) -> dict:
    """The protocol's typed answer for `q`, from the raw logits at its answer slots.

    `confidence` is the top option's probability — how concentrated the distribution is. It is
    an uncalibrated score over quantized weights, which is why the raw logits are returned too.
    """
    probs = softmax(logits)
    best = max(range(len(probs)), key=probs.__getitem__)
    conf = round(probs[best], 6)
    if q.type == "predicate":
        return {"type": "predicate", "name": q.name, "probability": round(probs[0], 6),
                "logits": [round(v, 4) for v in logits]}
    if q.type == "choice":
        return {"type": "choice", "name": q.name, "choice": q.options[best].value,
                "probabilities": [{"value": o.value, "probability": round(p, 6)}
                                  for o, p in zip(q.options, probs, strict=True)],
                "confidence": conf, "logits": [round(v, 4) for v in logits]}
    return {"type": "score", "name": q.name,
            "score": round(sum(i * p for i, p in enumerate(probs)), 6),
            "probabilities": [{"value": i, "label": o.label, "probability": round(p, 6)}
                              for i, (o, p) in enumerate(zip(q.options, probs, strict=True))],
            "confidence": conf, "logits": [round(v, 4) for v in logits]}

