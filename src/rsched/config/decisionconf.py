"""Decision-model config: the transports and the catalog behind the `decide` action.

A DECISION model (TypeSafe's Jev, OpenAI's Decisions API, the SemIf readout served on predator)
takes evidence and typed questions and returns probabilities over the answers it was offered —
never text. That is a different contract from a chat completion, so these are their own
catalog beside `endpoints:`/`models:` rather than a third chat kind: nothing that resolves a chat
role can reach one, and nothing that asks for a decision can reach a chat model.
See docs/decision-models.md.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from .base import BlankableStr, _Config

#: The two wire protocols. `openai` is OpenAI's `POST {base}/decisions` (input + a questions
#: LIST; text and inline images) — also what deploy/decision-server serves. `systemone` is
#: TypeSafe's `POST {base}/systemone` (state + a questions MAP; text only) — Jev, served by
#: TypeSafe itself and resold by OpenRouter under the same path.
DecisionProtocol = Literal["openai", "systemone"]
DECISION_PROTOCOLS = get_args(DecisionProtocol)
#: The protocols that carry images at all. Jev is text-only by its own documentation.
DECISION_MEDIA_PROTOCOLS = frozenset({"openai"})
#: The secrets-store variable a decision endpoint's key is read from when `key_var` is unset.
DECISION_KEY_VAR_DEFAULTS = {"openai": "OPENAI_API_KEY", "systemone": "TYPESAFE_API_KEY"}
#: A whole request's ceiling. Generous because a local model on a CPU fallback decides a dozen
#: photos in minutes, not seconds; a hosted one answers in well under one.
DEFAULT_DECISION_TIMEOUT_S = 300


class DecisionEndpointConfig(_Config):
    """One decision transport: a protocol, where it is served, and how it authenticates."""

    name: str = ""          # filled from the `decision_endpoints:` mapping key
    protocol: DecisionProtocol
    base_url: BlankableStr = ""
    api_key: BlankableStr = ""
    key_env_file: BlankableStr = ""
    key_var: BlankableStr = ""   # unset → DECISION_KEY_VAR_DEFAULTS[protocol]
    timeout_s: int = Field(DEFAULT_DECISION_TIMEOUT_S, ge=5, le=3600)

    @model_validator(mode="after")
    def _protocol_default_key_var(self):
        if not self.key_var:
            self.key_var = DECISION_KEY_VAR_DEFAULTS.get(self.protocol, "")
        return self


class DecisionModelConfig(_Config):
    """One catalog decision model: a provider model id bound to a decision endpoint."""

    name: str = ""          # filled from the `decision_models:` mapping key
    endpoint: str           # which configured decision endpoint serves it
    model: str              # the provider's model id (gpt-6-luna, typesafe/jev-1.13, …)
    # None = the protocol's default: on for `openai`, off for `systemone` (which cannot be on).
    multimodal: bool | None = None


def multimodal_effective(model: DecisionModelConfig, endpoint: DecisionEndpointConfig | None
                         ) -> bool:
    """Whether this model is sent images: its own flag, else its protocol's default — and never
    on a protocol that cannot carry them, whatever the flag says (the loader reports that).
    """
    if endpoint is None or endpoint.protocol not in DECISION_MEDIA_PROTOCOLS:
        return False
    return True if model.multimodal is None else model.multimodal


def decision_problems(cfg) -> list[str]:
    """The cross-entry checks `load_server_config` reports for the decision catalog."""
    problems: list[str] = []
    for name, ep in cfg.decision_endpoints.items():
        ep.name = name
    for name, mc in cfg.decision_models.items():
        mc.name = name
        ep = cfg.decision_endpoints.get(mc.endpoint)
        if ep is None:
            problems.append(f"decision_models.{name}: decision endpoint {mc.endpoint!r} is not "
                            "configured")
        elif mc.multimodal and ep.protocol not in DECISION_MEDIA_PROTOCOLS:
            problems.append(f"decision_models.{name}: multimodal is on, but the {ep.protocol} "
                            "protocol carries text only — images would never be sent")
    for key in ("decision_model", "decision_media_model"):
        value = getattr(cfg, key)
        if value and value not in cfg.decision_models:
            problems.append(f"{key}: {value!r} is not a decision model")
    media = cfg.decision_models.get(cfg.decision_media_model)
    if media is not None and not multimodal_effective(
            media, cfg.decision_endpoints.get(media.endpoint)):
        problems.append(f"decision_media_model: {cfg.decision_media_model!r} does not take "
                        "images")
    return problems
