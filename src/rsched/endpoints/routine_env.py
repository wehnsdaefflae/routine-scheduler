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


def llm_env_vars() -> set[str]:
    """The engine-injected endpoint env vars — these come from the instance's `endpoints:`
    config, not from the user's secret store, so the Settings 'needed secrets' list must not
    prompt for them as store secrets.
    """
    return {LLM_VAR, LLM_KEYS_VAR}


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
