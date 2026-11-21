"""Which INFERENCE ENDPOINT a routine's utils may use — the engine's answer to "where do I
send a completion?", injected per run.

Two env vars carry it to a util subprocess, both under the declared-only injection gate
(`utils_run._child_env`): `RSCHED_LLM` — non-secret endpoint metadata (base_url/kind/whether a
key exists, plus which endpoint each of the routine's model ROLES resolves to) — and
`RSCHED_LLM_KEYS` — {endpoint name: api key}, a credential (its name ends in KEYS, so the
util-authoring gate forces its declaration). Same shape as `machines.py`.

WHY: without it a util must hardcode a provider, so a routine configured for a LOCAL model
could ship private data to a cloud one. The routine's effective endpoint is engine knowledge a
caller cannot forge, so it rides the same channel as `RSCHED_ROUTINE`.

Metadata covers EVERY configured endpoint, but `RSCHED_LLM_KEYS` carries keys ONLY for the
endpoints this routine's own roles resolve to; any other key stays behind its own
`secret:<NAME>` grant row, which an injected blob of every key would route around.

Not what `utils_run.STRIP_VARS` forbids: that rule stops a util INHERITING the daemon's keys
under a provider's well-known name, invisibly. These vars are engine-named, must be DECLARED to
arrive, and carry the key of the routine's own configured endpoint. The provider names stay
stripped.
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


#: The same two vars for the DECISION catalog (D288), kept beside the inference pair so the two
#: cannot drift into two designs.
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
    # The CATALOG by name (as the decision half carries it), so a util can honour an `llm`
    # `model` override. An entry whose endpoint is not configured is left out rather than
    # offered. Keys stay ROLE endpoints only.
    catalog = {name: {"endpoint": mc.endpoint, "model": mc.model}
               for name, mc in sorted((server.models or {}).items())
               if mc.endpoint in (server.endpoints or {})}
    meta = {"routine": slug, "default": "tool_call", "roles": roles, "endpoints": endpoints,
            "models": catalog}
    return {LLM_VAR: json.dumps(meta, separators=(",", ":")),
            LLM_KEYS_VAR: json.dumps(keys, separators=(",", ":"))}


def decision_roles(models: dict[str, str], server: ServerConfig) -> dict[str, dict | None]:
    """Each DECISION role (`config.base.DECISION_ROLES`) → the catalog entry it resolves to, or
    None — the decision-catalog twin of `role_endpoints`.

    An unset role falls back to the INSTANCE default (`decision_model` /
    `decision_media_model`), as `endpoints/decisions.pick_decision_model` does, so a util
    reaches the same model the engine would. A role naming a model the catalog no longer
    carries is a warning and reads as None — never substituted.
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
    nothing". `RSCHED_DECISIONS_KEYS` carries keys for the ROLE endpoints only, as the
    inference half does.

    The PROTOCOL rides the metadata so a util can send the right wire body (or refuse
    clearly) instead of rediscovering the protocol by failing (F888).
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

    Separate from `endpoint_key` because an unset `key_var` falls back to
    `DECISION_KEY_VAR_DEFAULTS[protocol]`. Never raises: a missing key is only a warning.
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
