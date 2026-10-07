"""The `openai` decision protocol: `POST {base}/decisions` (OpenAI's Decisions API).

The shape, from OpenAI's guide (developers.openai.com, "Decisions", public beta 2026-10):

    {"model": "gpt-6-luna",
     "input": "<text>" | [{"role": "user", "content": [
                {"type": "input_text", "text": …},
                {"type": "input_image", "image_url": "data:image/png;base64,…"}]}],
     "questions": [{"type": "predicate"|"choice"|"score", "name": …, "instructions": …,
                    "choices": [{"value", "description"}] | "levels": [{"label", "description"}]}]}

    → {"answers": [{"type": "predicate", "name", "probability"}
                   | {"type": "choice", "name", "choice", "probabilities": [{"value",
                      "probability"}], "confidence"}
                   | {"type": "score", "name", "score", "probabilities": [{"value": <index>,
                      "label", "probability"}], "confidence"}
                   | {"type": "refusal", "name"}], "usage": {…}}

Images must be INLINE data URLs — hosted URLs and file ids are refused by the API — so each one
is read and encoded here, at send time, and never stored. deploy/decision-server serves this
same protocol over the model measured on predator, which is why one adapter covers both.
"""

from __future__ import annotations

import json

from ..config.decisionconf import DecisionEndpointConfig
from .base import (
    EndpointError,
    json_or_raise,
    post_json,
    raise_for_status,
    read_media_b64,
    resolve_api_key,
    with_retries,
)
from .decisions import (
    DEFAULT_BASE_URLS,
    Decision,
    DecisionAnswer,
    DecisionImage,
    DecisionQuestion,
)

_WIRE_TYPE = {"yes_no": "predicate", "choice": "choice", "score": "score"}


def evidence_text(evidence: str | dict | list) -> str:
    """Structured evidence as the text this protocol's `input` takes (its string form)."""
    return evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False,
                                                                 indent=1)


def wire_question(q: DecisionQuestion) -> dict:
    """One engine question in the protocol's own vocabulary."""
    out: dict = {"type": _WIRE_TYPE[q.type], "name": q.name, "instructions": q.question}
    if q.type == "choice":
        out["choices"] = [{"value": o.value, **({"description": o.description}
                                                if o.description else {})} for o in q.options]
    elif q.type == "score":
        out["levels"] = [{"label": o.value, **({"description": o.description}
                                               if o.description else {})} for o in q.options]
    return out


def _answer(q: DecisionQuestion, raw: dict, name: str) -> DecisionAnswer:
    kind = raw.get("type")
    if kind == "refusal":
        return DecisionAnswer(q.name, q.type, refused=True)
    try:
        if q.type == "yes_no":
            p = float(raw["probability"])
            return DecisionAnswer(q.name, q.type, {"yes": p, "no": round(1 - p, 6)},
                                  probability=p, confidence=max(p, 1 - p))
        rows = raw.get("probabilities") or []
        if q.type == "choice":
            probs = {str(r["value"]): float(r["probability"]) for r in rows}
            return DecisionAnswer(q.name, q.type, probs, choice=str(raw["choice"]),
                                  confidence=_float(raw.get("confidence")))
        probs = {q.options[int(r["value"])].value: float(r["probability"]) for r in rows}
        return DecisionAnswer(q.name, q.type, probs, score=float(raw["score"]),
                              confidence=_float(raw.get("confidence")))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise EndpointError(f"{name}: answer {q.name!r} does not have the {q.type} shape "
                            f"({exc!r}): {json.dumps(raw)[:300]}") from exc


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class OpenAIDecisions:
    """Adapter for OpenAI's Decisions API and anything serving its shape."""

    def __init__(self, cfg: DecisionEndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or DEFAULT_BASE_URLS["openai"]).rstrip("/")
        self.cfg = cfg

    def _key(self) -> str:
        # required=False: a self-hosted server on a trusted network may run without a token.
        return resolve_api_key(name=self.name, api_key=self.cfg.api_key, key_var=self.cfg.key_var,
                               key_env_file=self.cfg.key_env_file, required=False)

    def body(self, evidence: str | dict | list, images: list[DecisionImage],
             questions: list[DecisionQuestion], model: str) -> dict:
        """The request body — public so a test can pin the wire shape without a server."""
        text = evidence_text(evidence)
        if images:
            parts = ([{"type": "input_text", "text": text}] if text.strip() else []) + [
                {"type": "input_image",
                 "image_url": f"data:{img.media_type};base64,{read_media_b64(img.path)}"}
                for img in images]
            payload: str | list = [{"role": "user", "content": parts}]
        else:
            payload = text
        return {"model": model, "input": payload,
                "questions": [wire_question(q) for q in questions]}

    def decide(self, evidence: str | dict | list, images: list[DecisionImage],
               questions: list[DecisionQuestion], *, model: str, timeout: int) -> Decision:
        """POST the questions and normalise every answer (matched by name, never by order)."""
        body = self.body(evidence, images, questions, model)
        headers = {"Authorization": f"Bearer {self._key()}"}

        def call() -> dict:
            resp = post_json(f"{self.base_url}/decisions", body, headers, timeout,
                             name=self.name)
            raise_for_status(resp, self.name)
            return json_or_raise(resp, self.name)

        data = with_retries(call)
        by_name = {str(a.get("name")): a for a in data.get("answers") or []
                   if isinstance(a, dict)}
        missing = [q.name for q in questions if q.name not in by_name]
        if missing:
            raise EndpointError(f"{self.name}: no answer came back for {', '.join(missing)}: "
                                f"{json.dumps(data)[:300]}")
        usage_raw = data.get("usage") or {}
        usage: dict = {"in": int(usage_raw.get("input_tokens") or 0),
                       "out": int(usage_raw.get("output_tokens") or 0)}
        if usage_raw.get("cost") is not None:
            usage["cost"] = float(usage_raw["cost"])
        return Decision([_answer(q, by_name[q.name], self.name) for q in questions], usage,
                        model=str(data.get("model") or ""), provider=str(data.get("provider")
                                                                         or ""))
