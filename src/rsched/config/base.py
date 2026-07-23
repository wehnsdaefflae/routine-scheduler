"""Shared config vocabulary: the default sets, type aliases, the lenient pydantic base,
and the degrade-per-key validator every loader uses. Split from the old single config.py;
the public surface is unchanged (config/__init__ re-exports everything).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from ..paths import expand

DEFAULT_BUDGETS = {
    "max_turns": 60,
    # -1 = unlimited (default): a cumulative turn ceiling across ALL of a run's resume
    # windows (a conversation's whole life is the sum of its replies). max_turns bounds ONE
    # window (one reply); max_total_turns bounds the whole conversation. Scheduled routines
    # run a single window, so this is inert for them unless explicitly set.
    "max_total_turns": -1,
    "max_wall_clock_min": 45,
    # -1 = unlimited: for max_total_tokens this is the default (every finite cap we tried,
    # 500k then 1.5M, eventually cut off legitimate work). max_wall_clock_min and max_cost
    # also honor -1 = unlimited, so an operator can lift the time, token, and dollar ceilings
    # independently and let max_turns be the sole backstop. Set a positive number to bound
    # spend on a specific routine. max_cost is a whole-dollar ceiling on real provider spend
    # (usage["cost"], reported today only by metered endpoints like OpenRouter); -1 = no cap.
    "max_total_tokens": -1,
    "max_cost": -1,
    "max_subruns": 8,
    "max_subrun_depth": 2,
    # 480 (8h) deployment norm: a 5-minute default seeded a blocking-ask timeout trap into
    # every new routine (recurred on scheduler-improvement-research + global-utils-review,
    # each hand-fixed). Raised deployment-wide per config-optimizer q-20260717-191914-24.
    "ask_timeout_min": 480,
}
# The two-layer permission defaults a new routine gets when routine.yaml is silent.
# PERMISSIONS are conduct docs (library prose reaching the prompt when held);
# CAPABILITIES are the atomic machine-enforced surface (see grants.py) — gated action
# kinds, reserved utils, the write_util approval level, previous-run read depth. The two
# stay consistent via the web layer's cascades: activating a doc switches on what its
# `requires:` names; switching a capability off deactivates the docs requiring it.
# `communication` (discord), `run-history` depth and `shell` stay opt-in. There is NO
# self-modification permission: a run never edits its own recipe or routine.yaml — the
# routine-improver meta routine refines recipes centrally (its fs_write_roots covering
# the homes is the one engine-recognized unlock). Defaults added here AFTER routines
# exist reach them via bootstrap.ADOPT_PERMISSIONS (one-time, at boot).
DEFAULT_PERMISSIONS = ["util-authoring", "memory"]
DEFAULT_CAPABILITIES = {"actions": ["write_util", "memory_read", "memory_write"],
                        "utils": [], "confirm": "always", "runs": "none",
                        "workflows": "catalog"}
# TRAITS a new routine gets when creation picks none explicitly (the wizard normally
# preselects per task): reusable practice prose, adapted into the routine's own traits/
# at creation and referenced from the end of its main.md. The USER may add or remove one
# later (traits.py — a later add copies the library text verbatim, since only creation
# adapts); a RUN never changes its own set, and may only CONSULT an unheld module for the
# current run (`read_trait`). Improvement passes are NOT part of a
# routine's own traits: the routine-improver meta routine runs them across all routines
# and conversations (honoring each one's `improve: false` opt-out).
DEFAULT_TRAITS = ["ask-policy", "global-utils", "ledger-discipline", "web-research"]
# Each routine picks its own models: the MAIN orchestrator loop, the model spawned
# SUBROUTINEs run their main loop on, the model TOOL_CALLs (the `llm` action) use, and an
# OPTIONAL UNCENSORED model a refused `llm` tool-call is re-referred to. The uncensored role
# is opt-in and has NO system_model fallback: a routine refers a refusal ONLY when it has
# explicitly configured this role (e.g. to a Nano-GPT abliterated model). Leaving it unset
# preserves the previous behaviour exactly.
MODEL_KINDS = ("main", "subroutine", "tool_call", "uncensored")
# How much of the model's thinking lands ON PAPER — the persistent prose channel (`say`,
# plus a notes-file discipline at the top stop). Ordered stops, not a continuum: models
# follow qualitatively distinct contracts, not "verbosity 0.7". Composer wording per stop
# lives in engine/deliberation.py. Distinct from a model's `effort` (ephemeral thinking,
# thrown away between turns); deliberation is ink, effort is scratch paper. The value
# lives in TUNING (tuning.yaml, recipe-classed: the improver may edit it under its
# fs_write_root; routine.yaml stays the user's sealed authority config). User-set via
# the routine page / wizard / conversation panel; mid-run via control.json.
DELIBERATION_LEVELS = ("terse", "standard", "deliberate", "think-on-paper")
DEFAULT_DELIBERATION = "standard"
CONVERSATION_DELIBERATION = "deliberate"  # chat is judgment-heavy — context on paper by default
# Endpoints are model TRANSPORTS, never a second harness. "claude-cli" is the Claude Code
# CLI in fully stripped print mode (tools off, our system prompt replaces its own) — a
# subscription-billed completion function; the engine remains the only agent loop.
EndpointKind = Literal["openai", "anthropic", "claude-cli"]
SchemaMode = Literal["json_schema", "json_object", "ollama_native", "none"]
ENDPOINT_KINDS = get_args(EndpointKind)
SCHEMA_MODES = get_args(SchemaMode)
# Kinds whose models are multimodal by construction, so a catalog model on an endpoint of this
# kind defaults to native image/PDF input unless the model says otherwise (the `anthropic`
# Messages API and the subscription CLI only ever serve Claude, which takes image blocks).
# `openai` varies per model (GLM is text-only, GPT-4o/Gemini aren't) so it defaults OFF — the
# user flips `multimodal` on for the specific catalog model (see ModelConfig).
NATIVE_MM_KINDS = {"anthropic", "claude-cli"}
# The secrets-store / env-file variable an endpoint's key is looked up under when the
# config doesn't set `key_var`. Per KIND: an openai endpoint must never default to the
# Anthropic key. claude-cli has no entry — it authenticates via the subscription token
# (`credentials_env` / CLAUDE_CODE_OAUTH_TOKEN), never key_var.
KEY_VAR_DEFAULTS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
# The output cap a resolved model falls back to when neither the catalog model nor its
# endpoint sets max_tokens. Generous on purpose: reasoning models need room to think AND
# answer — a provider's small default can swallow the content entirely. Settings flags
# models still riding this fallback so the real per-model limit gets configured.
DEFAULT_MODEL_MAX_TOKENS = 16_384

# YAML-friendly coercions: a bare `key:` (null) reads as the empty string; path strings
# expand `~` and $VARS.
BlankableStr = Annotated[str, BeforeValidator(lambda v: "" if v is None else v)]
HomePath = Annotated[Path, BeforeValidator(lambda v: expand(v) if isinstance(v, str) else v)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True,
                              coerce_numbers_to_str=True)


def _known_tz(v: str) -> str:
    """Shared tz validator: a typo'd zone must surface as a config problem at load/save
    time, not as a ZoneInfoNotFoundError inside the scheduler tick.
    """
    import zoneinfo

    try:
        zoneinfo.ZoneInfo(v)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {v!r}") from exc
    return v


def _pop(data: dict, loc: tuple) -> None:
    """Remove the value at a (possibly nested) error location from the raw input."""
    node: object = data
    for key in loc[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict) and loc:
        node.pop(loc[-1], None)


def _validate_lenient(model: type[_Config], data: dict, problems: list[str]):
    """model_validate that degrades per key: report every invalid key, drop it (or its
    parent, when a required subfield is missing) and retry so the rest still loads.
    """
    for round_no in range(4):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            for err in exc.errors():
                if round_no == 0:  # later rounds only see errors derived from a drop
                    where = ".".join(str(p) for p in err["loc"]) or "(root)"
                    problems.append(f"{where}: {err['msg'].removeprefix('Value error, ')}")
                loc = err["loc"][:-1] if err["type"] == "missing" else err["loc"]
                if not loc:
                    return None
                _pop(data, loc)
    return None
