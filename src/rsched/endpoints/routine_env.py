"""Which INFERENCE ENDPOINT a routine's utils may use — the engine's answer to "where do I
send a completion?", injected per run.

Two env vars carry it to a util subprocess, both under the declared-only injection gate
(`utils_run._child_env`): `RSCHED_LLM` — non-secret endpoint metadata (base_url/kind/whether a
key exists, plus which endpoint each of the routine's model ROLES resolves to) — and
`RSCHED_LLM_KEYS` — {endpoint name: api key}, a credential (its name ends in KEYS, so the
util-authoring gate forces its declaration). Same shape as `machines.py`, deliberately: an
operator who has read one has read both.

WHY IT EXISTS. Every LLM-calling util used to HARDCODE a provider, because nothing told it which
endpoint its own routine runs on — a util could be handed a key named `OPENROUTER_UTIL_KEY` but
never the fact that this instance serves its models through a local gateway. So a routine
configured for a LOCAL model still shipped its prompt to a cloud one, which for the files
`folder-reorg` reads (private scans that must never leave the box) is not a cost problem but a
privacy breach. The routine's effective endpoint is engine knowledge, it cannot be forged by a
caller, and it is exactly what `RSCHED_ROUTINE` is for WHO — so it rides the same channel.

WHAT A KEY IS NOT GIVEN FOR. Metadata covers EVERY configured endpoint (so a util can see that a
local, keyless one exists and reach it), but `RSCHED_LLM_KEYS` carries keys ONLY for the
endpoints this routine's own roles resolve to. A key for any other endpoint stays where the
operator decided it: the named store secret with its own four-state `secret:<NAME>` grant row. An
engine-injected blob of every endpoint key would route around that decision, which is not a
widening a run gets to make for him.

WHY THIS IS NOT THE THING `STRIP_VARS` FORBIDS. `utils_run.STRIP_VARS` scrubs
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_BASE_URL` and friends from every jailed
subprocess unconditionally, so that no util "inherit[s] the orchestrator's keys and silently
mis-bill[s] or use[s] the wrong account". That rule is about INHERITANCE: a credential arriving
under a provider's well-known name, from whatever account the daemon happened to be configured
with, invisible in the util's own header. This is the opposite on every count — the var is
named for this engine, it must be DECLARED to arrive at all (so it is visible in the header the
operator reads and in `rsched validate`), and what it carries is the key for the endpoint the
routine is configured to use, resolved per run. A util that reads it bills the account its own
routine runs on, which is what STRIP_VARS protects and could not express. Those provider names
stay stripped: nothing here puts a key back under one.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .base import EndpointError, resolve_api_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import EndpointConfig, ServerConfig

log = logging.getLogger("rsched.endpoints.routine_env")

# The two env vars the engine injects for a routine's reachable inference endpoints. Kept out of
# the Settings "needed secrets" prompt (like the machine vars and OAuth access-token vars) — the
# user never SETS these; they are assembled from `endpoints:` + each endpoint's own key ladder.
LLM_VAR = "RSCHED_LLM"            # env var NAME (non-secret endpoint metadata)
LLM_KEYS_VAR = "RSCHED_LLM_KEYS"  # env var NAME (carries the per-endpoint api keys)

#: The model roles a routine can bind (config/routine.py `models:`). `supervisor` is deliberately
#: absent: it is the escalation ladder's overseer, resolved through `main` while unset, and a util
#: has no business sending work to it.
ROLES = ("main", "tool_call", "uncensored")


#: The same two vars for the DECISION catalog (D288, goal b6). A util reaching a decision
#: endpoint had no way to learn which one its routine runs on — the inference half has had that
#: since this module shipped, and the asymmetry is why `decide` could only ever be an ACTION:
#: the engine knew the catalog and a util did not. One `model` util with both capabilities needs
#: both injections, so they live here, beside each other, deliberately: a reader who has
#: understood one has understood the other, and they cannot drift into two designs.
DECISIONS_VAR = "RSCHED_DECISIONS"            # non-secret catalog metadata
DECISIONS_KEYS_VAR = "RSCHED_DECISIONS_KEYS"  # the per-endpoint api keys


def llm_env_vars() -> set[str]:
    """The engine-injected endpoint env vars — these come from the instance's `endpoints:` and
    `decision_endpoints:` config, not from the user's secret store, so the Settings 'needed
    secrets' list must not prompt for them as store secrets.
    """
    return {LLM_VAR, LLM_KEYS_VAR, DECISIONS_VAR, DECISIONS_KEYS_VAR}


def endpoint_key(cfg: EndpointConfig) -> str:
    """One endpoint's api key through the SHARED credential ladder (`resolve_api_key`: inline
    key, then `key_var` in the store, then `key_var` in `key_env_file`), or "" when there is
    none. Never raises: an endpoint whose configured env file has lost its key is a warning for
    the operator, not a failed util call — and "none", the placeholder bearer keyless local
    backends ignore, is not a key either.
    """
    try:
        key = resolve_api_key(name=cfg.name, api_key=cfg.api_key, key_var=cfg.key_var,
                              key_env_file=cfg.key_env_file, required=False)
    except EndpointError as exc:
        log.warning("endpoint key: %s", exc)
        return ""
    return "" if key == "none" else key


def endpoint_public(cfg: EndpointConfig, *, key_set: bool, name: str | None = None) -> dict:
    """Non-secret metadata for one endpoint — what reaches a util (and could reach a UI card).
    Never the key itself; `key_set` only reports whether one resolved. `name` defaults to
    `cfg.name` (filled from the `endpoints:` mapping key by load_server_config) but can be
    overridden with that key, so resolution never depends on a post-load step.
    """
    return {"name": name if name is not None else cfg.name,
            "kind": cfg.kind, "base_url": cfg.base_url,
            "key_var": cfg.key_var, "has_key": key_set}


def role_endpoints(models: dict[str, str], server: ServerConfig) -> dict[str, dict | None]:
    """Each of the routine's model ROLES → the endpoint and provider model id it resolves to,
    or None for a role that resolves to nothing (an unset `uncensored` is the normal case, and
    it must read as absent rather than as an invented binding). A role naming a catalog model
    that no longer exists is a warning and reads as None too — a util's default endpoint going
    missing must be visible, never substituted.
    """
    from ..config.base import role_default_model

    out: dict[str, dict | None] = {}
    for role in ROLES:
        name = models.get(role)
        if not name and role != "uncensored":
            name, _effort = role_default_model(server, role)
        if not name:
            out[role] = None
            continue
        mc = server.models.get(name)
        if mc is None or mc.endpoint not in server.endpoints:
            log.warning("role %r names model %r, which resolves to no configured endpoint",
                        role, name)
            out[role] = None
            continue
        out[role] = {"catalog": name, "endpoint": mc.endpoint, "model": mc.model}
    return out


def llm_env_for_routine(slug: str, models: dict[str, str],
                        server: ServerConfig) -> dict[str, str]:
    """The engine injection: this routine's effective inference endpoints → the env vars its
    utils receive. `RSCHED_LLM` always carries every configured endpoint plus the role map, so
    a util can tell "my routine has no uncensored model" from "I was told nothing";
    `RSCHED_LLM_KEYS` carries keys for the role endpoints only (see the module docstring).
    """
    roles = role_endpoints(models or {}, server)
    keyed = {r["endpoint"] for r in roles.values() if r}
    keys: dict[str, str] = {}
    endpoints: dict[str, dict] = {}
    for name, cfg in sorted((server.endpoints or {}).items()):
        key = endpoint_key(cfg)
        endpoints[name] = endpoint_public(cfg, key_set=bool(key), name=name)
        if key and name in keyed:
            keys[name] = key
    meta = {"routine": slug, "default": "tool_call", "roles": roles, "endpoints": endpoints}
    return {LLM_VAR: json.dumps(meta, separators=(",", ":")),
            LLM_KEYS_VAR: json.dumps(keys, separators=(",", ":"))}


def decision_roles(models: dict[str, str], server: ServerConfig) -> dict[str, dict | None]:
    """Each DECISION role (`config.base.DECISION_ROLES`) → the catalog entry it resolves to, or
    None — the decision-catalog twin of `role_endpoints`.

    An unset role falls back to the INSTANCE default (`decision_model` /
    `decision_media_model`), which is what `endpoints/decisions.pick_decision_model` does when a
    call names no model: a util told only about the routine's own bindings would reach a
    different model than the engine does for the same routine, which is the kind of divergence
    that makes a util a second implementation rather than a second front door.

    A role naming a model the catalog no longer carries is a warning and reads as None — never
    substituted, for the reason `role_endpoints` gives: a default going missing must be visible.
    """
    from ..config.base import DECISION_ROLES

    out: dict[str, dict | None] = {}
    defaults = {"decision": server.decision_model,
                "decision_media": server.decision_media_model}
    for role in DECISION_ROLES:
        name = (models or {}).get(role) or defaults.get(role) or ""
        if not name:
            out[role] = None
            continue
        mc = (server.decision_models or {}).get(name)
        if mc is None or mc.endpoint not in (server.decision_endpoints or {}):
            log.warning("decision role %r names model %r, which resolves to no configured "
                        "decision endpoint", role, name)
            out[role] = None
            continue
        out[role] = {"catalog": name, "endpoint": mc.endpoint, "model": mc.model,
                     "protocol": server.decision_endpoints[mc.endpoint].protocol}
    return out


def decision_env_for_routine(slug: str, models: dict[str, str],
                             server: ServerConfig) -> dict[str, str]:
    """This routine's effective DECISION catalog → the env vars its utils receive (D288).

    `RSCHED_DECISIONS` carries every configured decision endpoint and model plus the role map,
    so a util can tell "this instance has no media-capable decision model" from "I was told
    nothing". `RSCHED_DECISIONS_KEYS` carries keys for the ROLE endpoints only, exactly as the
    inference half does: a blob of every endpoint key would route around the operator's own
    `secret:<NAME>` decision, which is not a widening a run gets to make for him.

    **The PROTOCOL rides the metadata, and that is not decoration.** F888 cost nine days because
    the two documented ways of passing structured evidence were both dead on the default decision
    model, and the 0.535.2 fix landed in the `openai` adapter while all three of this instance's
    models speak `systemone`. A util that cannot see which protocol it is talking to can only
    rediscover that by failing; told it, it can send the right wire body or refuse clearly.
    """
    roles = decision_roles(models or {}, server)
    keyed = {r["endpoint"] for r in roles.values() if r}
    keys: dict[str, str] = {}
    endpoints: dict[str, dict] = {}
    for name, cfg in sorted((server.decision_endpoints or {}).items()):
        key = decision_endpoint_key(cfg)
        endpoints[name] = {"name": name, "protocol": cfg.protocol,
                           "base_url": cfg.base_url, "key_var": cfg.key_var,
                           "has_key": bool(key), "timeout_s": cfg.timeout_s}
        if key and name in keyed:
            keys[name] = key
    catalog = {name: {"endpoint": mc.endpoint, "model": mc.model}
               for name, mc in sorted((server.decision_models or {}).items())}
    meta = {"routine": slug, "default": "decision", "roles": roles,
            "endpoints": endpoints, "models": catalog}
    return {DECISIONS_VAR: json.dumps(meta, separators=(",", ":")),
            DECISIONS_KEYS_VAR: json.dumps(keys, separators=(",", ":"))}


def decision_endpoint_key(cfg) -> str:
    """One DECISION endpoint's api key through its own ladder, or "".

    Separate from `endpoint_key` because the fallback var differs: a decision endpoint whose
    `key_var` is unset falls back to `DECISION_KEY_VAR_DEFAULTS[protocol]`, which the inference
    config has no equivalent of. Never raises, for the same reason: a missing key is a warning
    for the operator, not a failed util call.
    """
    from ..config.decisionconf import DECISION_KEY_VAR_DEFAULTS

    key_var = cfg.key_var or DECISION_KEY_VAR_DEFAULTS.get(cfg.protocol, "")
    try:
        key = resolve_api_key(name=cfg.name, api_key=cfg.api_key, key_var=key_var,
                              key_env_file=cfg.key_env_file, required=False)
    except EndpointError as exc:
        log.warning("decision endpoint key: %s", exc)
        return ""
    return "" if key == "none" else key
