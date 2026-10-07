"""The `systemone` decision protocol: `POST {base}/systemone` (TypeSafe's Jev).

The shape, from docs.typesafe.ai/api.md and OpenRouter's Jev tutorial (2026-10):

    {"model": "jev-latest" | "typesafe/jev-1.13",
     "state": <string | object | array>,
     "questions": {"<name>": {"type": "noul"|"choice"|"score", "instructions": …,
                              "criteria": {"<option>": "<meaning>"} | ["<level>", …]}}}

    → {"answers": {"<name>": {"type": "noul", "noul": <P(yes)>}
                             | {"type": "choice", "choice", "confidence",
                                "probabilities": {"<option>": p}}
                             | {"type": "score", "score", "confidence",
                                "probabilities": {"<index>": p}, "legend": {…}}},
       "usage": {"input_tokens", "output_tokens", "cost"?}}

Served by TypeSafe itself (`https://api.typesafe.ai/v1`, a TypeSafe key) and resold by
OpenRouter under the same path (`https://openrouter.ai/api/v1`, an OpenRouter key, model
`typesafe/jev-1.13`). TEXT ONLY: Jev's documentation refuses image, audio and video input, so an
image reaching this adapter is a routing defect and is refused before anything is sent.
"""

from __future__ import annotations

import json

from ..config.decisionconf import DecisionEndpointConfig
from .base import (
    EndpointError,
    json_or_raise,
    post_json,
    raise_for_status,
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

_WIRE_TYPE = {"yes_no": "noul", "choice": "choice", "score": "score"}


def wire_question(q: DecisionQuestion) -> dict:
    """One engine question in Jev's vocabulary. A choice's criteria map every option to its
    meaning (the option itself when none was given); a score's are the levels, lowest first.
    """
    out: dict = {"type": _WIRE_TYPE[q.type], "instructions": q.question}
    if q.type == "choice":
        out["criteria"] = {o.value: o.description or o.value for o in q.options}
    elif q.type == "score":
        out["criteria"] = [f"{o.value}: {o.description}" if o.description else o.value
                           for o in q.options]
    return out


def _answer(q: DecisionQuestion, raw: dict, name: str) -> DecisionAnswer:
    try:
        if q.type == "yes_no":
            p = float(raw["noul"])
            return DecisionAnswer(q.name, q.type, {"yes": p, "no": round(1 - p, 6)},
                                  probability=p, confidence=max(p, 1 - p))
        rows = raw.get("probabilities") or {}
        conf = raw.get("confidence")
        confidence = float(conf) if isinstance(conf, (int, float)) else None
        if q.type == "choice":
            return DecisionAnswer(q.name, q.type, {str(k): float(v) for k, v in rows.items()},
                                  choice=str(raw["choice"]), confidence=confidence)
        probs = {q.options[int(k)].value: float(v) for k, v in rows.items()}
        return DecisionAnswer(q.name, q.type, probs, score=float(raw["score"]),
                              confidence=confidence)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise EndpointError(f"{name}: answer {q.name!r} does not have the {q.type} shape "
                            f"({exc!r}): {json.dumps(raw)[:300]}") from exc


class SystemOneDecisions:
    """Adapter for TypeSafe's System One API, direct or through OpenRouter."""

    def __init__(self, cfg: DecisionEndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or DEFAULT_BASE_URLS["systemone"]).rstrip("/")
        self.cfg = cfg

    def _key(self) -> str:
        return resolve_api_key(name=self.name, api_key=self.cfg.api_key, key_var=self.cfg.key_var,
                               key_env_file=self.cfg.key_env_file, required=True)

    @staticmethod
    def body(evidence: str | dict | list, questions: list[DecisionQuestion], model: str) -> dict:
        """The request body — public so a test can pin the wire shape without a server."""
        return {"model": model, "state": evidence,
                "questions": {q.name: wire_question(q) for q in questions}}

    def decide(self, evidence: str | dict | list, images: list[DecisionImage],
               questions: list[DecisionQuestion], *, model: str, timeout: int) -> Decision:
        """POST the questions; answers come back under the names they were asked by."""
        if images:
            raise EndpointError(f"{self.name}: the systemone protocol takes text only — "
                                f"{len(images)} image(s) cannot be sent")
        body = self.body(evidence, questions, model)
        headers = {"Authorization": f"Bearer {self._key()}"}

        def call() -> dict:
            resp = post_json(f"{self.base_url}/systemone", body, headers, timeout,
                             name=self.name)
            raise_for_status(resp, self.name)
            return json_or_raise(resp, self.name)

        data = with_retries(call)
        answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        missing = [q.name for q in questions if not isinstance(answers.get(q.name), dict)]
        if missing:
            raise EndpointError(f"{self.name}: no answer came back for {', '.join(missing)}: "
                                f"{json.dumps(data)[:300]}")
        usage_raw = data.get("usage") or {}
        usage: dict = {"in": int(usage_raw.get("input_tokens") or 0),
                       "out": int(usage_raw.get("output_tokens") or 0)}
        if usage_raw.get("cost") is not None:
            usage["cost"] = float(usage_raw["cost"])
        return Decision([_answer(q, answers[q.name], self.name) for q in questions], usage,
                        model=str(data.get("model") or ""),
                        provider=str(data.get("provider") or ""))
