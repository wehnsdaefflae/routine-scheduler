"""Decision-model transports: evidence and typed questions in, probabilities out.

The decision counterpart of `ChatEndpoint` (docs/decision-models.md). A decision model answers
only what it was asked, over only the answers it was offered: a yes/no as P(yes), a choice as a
distribution over the declared options, a score as a probability-weighted position on declared
levels. There is no text to parse, so there is no empty reply, no refusal hiding in prose and no
malformed JSON — the failure modes `engine/llmaction.py` spends most of its length on.

Two wire protocols, one adapter each, both normalised to the dataclasses below so nothing above
this layer knows which one answered: `openai` (decisions_openai.py — OpenAI's Decisions API and
the predator server in deploy/decision-server, the one that carries images) and `systemone`
(decisions_systemone.py — TypeSafe's Jev, direct or through OpenRouter, text only).

Transports, never a harness: one stateless request per call, no retries beyond the shared
`with_retries`, and the call recorded through the same instrumentation seam every chat
completion passes (so it shows in the LLM activity dock while it runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .base import EndpointError

if TYPE_CHECKING:
    from ..config.decisionconf import DecisionEndpointConfig

#: The three answer types the engine speaks — the plain-words names a run writes. Each adapter
#: maps them onto its protocol's own (`predicate`/`noul`, `choice`, `score`).
ANSWER_TYPES = ("yes_no", "choice", "score")
#: Where a protocol is served when `base_url` is blank — each vendor's own API root.
DEFAULT_BASE_URLS = {"openai": "https://api.openai.com/v1",
                     "systemone": "https://api.typesafe.ai/v1"}


@dataclass
class DecisionOption:
    """One answer a question offers: what the answer names it (`value`) and what it means."""

    value: str
    description: str = ""


@dataclass
class DecisionQuestion:
    """One typed question. `options` are the choices, or the score levels LOWEST FIRST; a
    yes/no question takes none.
    """

    name: str
    type: str
    question: str
    options: list[DecisionOption] = field(default_factory=list)


@dataclass
class DecisionImage:
    """An image the evidence includes, read and encoded only at send time."""

    path: Path
    media_type: str


@dataclass
class DecisionAnswer:
    """One question's typed answer, normalised across protocols.

    `probabilities` is keyed by the option VALUES that were sent (yes_no: "yes"/"no"), in the
    order sent. `probability` is P(yes) for a yes_no; `choice` the most probable value for a
    choice; `score` the probability-weighted level INDEX for a score, 0 being the first level.
    `confidence` is the provider's own figure where it reports one.
    """

    name: str
    type: str
    probabilities: dict[str, float] = field(default_factory=dict)
    probability: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    refused: bool = False


@dataclass
class Decision:
    """A whole response: every question's answer, the usage, and who served it."""

    answers: list[DecisionAnswer]
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    model: str = ""       # the snapshot that served the call, when the provider names it
    provider: str = ""


class DecisionEndpoint(Protocol):
    """What both adapters implement: one stateless decision request."""

    name: str

    def decide(self, evidence: str | dict | list, images: list[DecisionImage],
               questions: list[DecisionQuestion], *, model: str, timeout: int) -> Decision:
        """Answer every question over the evidence (and images, where the protocol takes them)."""
        ...


@dataclass
class DecisionRef:
    """A resolved catalog entry: what to call, where, and whether it may be sent images."""

    name: str
    endpoint: str
    model: str
    protocol: str
    multimodal: bool


def make_decision_endpoint(cfg: DecisionEndpointConfig) -> DecisionEndpoint:
    """The adapter for one configured decision endpoint."""
    from .decisions_openai import OpenAIDecisions
    from .decisions_systemone import SystemOneDecisions

    adapters: dict[str, type] = {"openai": OpenAIDecisions, "systemone": SystemOneDecisions}
    if cfg.protocol not in adapters:
        raise EndpointError(f"{cfg.name}: unknown decision protocol {cfg.protocol!r}")
    return adapters[cfg.protocol](cfg)


def resolve_decision(server, name: str) -> tuple[DecisionEndpoint, DecisionRef]:
    """A decision-catalog NAME → its adapter and resolved ref."""
    from ..config.decisionconf import multimodal_effective

    mc = server.decision_models.get(name)
    if mc is None:
        raise EndpointError(f"decision model {name!r} is not configured")
    ep = server.decision_endpoints.get(mc.endpoint)
    if ep is None:
        raise EndpointError(f"decision model {name!r}: its endpoint {mc.endpoint!r} is not "
                            "configured")
    ref = DecisionRef(name=name, endpoint=ep.name, model=mc.model, protocol=ep.protocol,
                      multimodal=multimodal_effective(mc, ep))
    return make_decision_endpoint(ep), ref


def default_decision_model(server, roles: dict | None, *, images: bool) -> str:
    """The model a call that names none goes to, or "" when none is configured.

    The routine's own roles come before the instance defaults, and the first configured model
    that can CARRY the call wins: a text call asks the routine's `decision`, then the instance
    `decision_model`; a call with images asks the routine's `decision_media`, then its
    `decision` if that one takes images, then the instance `decision_media_model`, then
    `decision_model`. A text-only choice is passed over for an image call rather than refused,
    so a routine that picked Jev for its text still has its photos judged.
    """
    roles = roles or {}
    if not images:
        candidates = [roles.get("decision"), server.decision_model]
    else:
        candidates = [roles.get("decision_media"), roles.get("decision"),
                      server.decision_media_model, server.decision_model]
    named = [c for c in candidates if c]
    for name in named:
        if not images or _takes_images(server, name):
            return name
    return named[0] if named else ""


def pick_decision_model(server, name: str | None, *, images: bool, roles: dict | None = None
                        ) -> tuple[DecisionEndpoint, DecisionRef]:
    """The model a call goes to: the one it names, else `default_decision_model` for its payload
    under the routine's `roles` (its `models:` map).

    Raises a TEACHING EndpointError — every alternative named — when the choice cannot carry the
    call: a name not in the catalog, no default configured, or images for a text-only model.
    """
    catalog = server.decision_models
    if name:
        if name not in catalog:
            raise EndpointError(f"{name!r} is not a decision model. Decision models: "
                                f"{', '.join(sorted(catalog)) or 'none configured'}")
        chosen = name
    else:
        chosen = default_decision_model(server, roles, images=images)
        if not chosen:
            raise EndpointError("no decision model is set for this call — name one with "
                                "`model`, or have the user set this routine's decision role "
                                "(routine page → Models) or the instance default (Settings → "
                                f"Decision endpoints): {', '.join(sorted(catalog)) or 'none'}")
    endpoint, ref = resolve_decision(server, chosen)
    if images and not ref.multimodal:
        takes = [n for n in sorted(catalog) if _takes_images(server, n)]
        raise EndpointError(f"decision model {chosen!r} takes text only, and this call carries "
                            f"images. Models that take images: {', '.join(takes) or 'none'} — "
                            "name one with `model`, or decide on a text description instead")
    return endpoint, ref


def _takes_images(server, name: str) -> bool:
    try:
        return resolve_decision(server, name)[1].multimodal
    except EndpointError:
        return False


def decision_catalog(server, roles: dict | None = None) -> list[dict]:
    """Every decision model as one row — what `list_models` and CAPABILITIES show a run. The
    `default` marks are what a call naming no model gets under these routine `roles`.
    """
    text_default = default_decision_model(server, roles, images=False)
    image_default = default_decision_model(server, roles, images=True)
    rows: list[dict] = []
    for name in sorted(server.decision_models):
        try:
            _, ref = resolve_decision(server, name)
        except EndpointError as exc:
            rows.append({"name": name, "error": str(exc)})
            continue
        defaults = [label for label, chosen in (("default", text_default),
                                                ("default for images", image_default))
                    if chosen == name]
        rows.append({"name": name, "endpoint": ref.endpoint, "model": ref.model,
                     "protocol": ref.protocol, "images": ref.multimodal,
                     **({"default": " · ".join(defaults)} if defaults else {})})
    return rows


def decide(endpoint: DecisionEndpoint, ref: DecisionRef, evidence: str | dict | list,
           images: list[DecisionImage], questions: list[DecisionQuestion], *, timeout: int,
           purpose: str) -> Decision:
    """One decision request, recorded like a chat completion while it is in flight."""
    from .instrument import observe_call

    return observe_call(lambda: endpoint.decide(evidence, images, questions, model=ref.model,
                                                timeout=timeout),
                        endpoint=ref.endpoint, model=ref.model, purpose=purpose, kind="decide")
