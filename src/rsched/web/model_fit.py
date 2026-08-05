"""Model-window fit for the pickers (R112/R128): can this catalog model run the harness?

The engine sizes its hard input ceiling in the token domain (`engine/history.py`
`window_ceiling_chars`: `window_tokens − max_output_tokens`, converted to chars at the
conservative input density) — a model whose ceiling is not positive cannot complete a
single turn: the output reservation alone fills the window, and the request 400s with
context_length_exceeded no matter how far compaction and the clamp trim (F265's terminal
form). Conversation-create and model-change REFUSE that class up front instead of letting
the first reply die on it; models that fit but leave a tight input budget are labeled in
the pickers (WARN), not refused — compaction handles tight, nothing handles impossible.

Effective window figures resolve the way EndpointRegistry does (model value, else the
serving endpoint's default) WITHOUT instantiating a transport — the pickers must work
while an endpoint is unreachable or half-configured.
"""

from __future__ import annotations

from ..config.base import DEFAULT_MODEL_MAX_TOKENS
from ..engine.history import CHARS_PER_TOKEN, window_ceiling_chars

# Below this input ceiling (chars) a model runs but compacts from the first replies: a
# conversation's recipe + traits alone are ~25k chars before the capability catalog and any
# actual work land on top. Label it in the picker; the user may still pick it.
TIGHT_INPUT_CHARS = 60_000


def effective_window_pair(mc, ep) -> tuple[int, int]:
    """(context_chars, max_output_tokens) for one catalog model + its serving endpoint
    config (ep may be None) — the same resolution EndpointRegistry.resolve performs,
    minus the transport.
    """
    context = mc.context_chars or (ep.context_chars if ep else 0) or 100_000
    max_out = mc.max_tokens or (ep.max_tokens if ep else None) or DEFAULT_MODEL_MAX_TOKENS
    return context, max_out


def effective_window(server, name: str) -> tuple[int, int]:
    """`effective_window_pair`, keyed by catalog model name."""
    mc = server.models[name]
    return effective_window_pair(mc, server.endpoints.get(mc.endpoint))


def fit_fields(mc, ep) -> dict:
    """The window-sizing fields every picker payload carries for one model — the ONE
    derivation behind /api/settings/models and the conversation detail's catalog_meta.
    """
    context, max_out = effective_window_pair(mc, ep)
    ceiling = int(window_ceiling_chars(context, max_out))
    return {
        "context_chars": context,
        "context_tokens": int(context / CHARS_PER_TOKEN),
        "max_output_tokens": max_out,
        "input_ceiling_chars": ceiling,
        "fit": ("impossible" if ceiling <= 0
                else "tight" if ceiling < TIGHT_INPUT_CHARS else "ok"),
    }


def model_window_problem(server, name: str) -> str | None:
    """The refusal message when `name` mathematically cannot run the harness, else None.
    Callers 400 with it at conversation-create and model-change; a name not in the catalog
    is the caller's own (earlier) validation, not this one's.
    """
    context, max_out = effective_window(server, name)
    if window_ceiling_chars(context, max_out) > 0:
        return None
    window_tokens = int(context / CHARS_PER_TOKEN)
    return (f"model {name!r} cannot run a single turn: its context window "
            f"(~{window_tokens:,} tokens) minus its max output tokens ({max_out:,}) "
            "leaves no room for input — every completion would overflow the window. "
            "Pick a larger-window model, or lower this model's max_tokens under "
            "Settings → Models.")


def window_meta(server) -> dict[str, dict]:
    """Per-model window metadata for the pickers: {name: fit_fields} with fit "ok" |
    "tight" | "impossible" (see `fit_fields`).
    """
    return {name: fit_fields(mc, server.endpoints.get(mc.endpoint))
            for name, mc in server.models.items()}
