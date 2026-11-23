"""What a util subprocess is ALLOWED TO SEE — the environment assembled for one call.

The security boundary for credentials, declared-only by construction: a connection token
reaches a util iff the routine binds the provider AND the util declares the var; a machine key
iff the routine binds the machine; a routine-scoped secret shadows a central one (D103). An
unbound connection becomes an access REQUEST, never a silent empty string.
"""

from __future__ import annotations

import json
import logging

from .. import machines, utils_run
from ..machine_queue import REMOTE_UTIL
from ..oauth import store as oauth_store
from .run_context import RunContext

log = logging.getLogger("rsched.engine.exec_env")


def _bound_connections(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE OAuth bindings, {provider: account}: the config bindings plus
    this run's one-time connection grants (account in ctx.grant_args). The one reading of "does
    this run hold a connection", so token injection and request routing cannot disagree.
    """
    bound = dict(ctx.routine.connections or {})
    for eid in sorted(ctx.granted_now):
        if eid.startswith("connection:"):
            provider = eid.partition(":")[2]
            bound.setdefault(provider, str(ctx.grant_args.get(eid) or ""))
    return bound


def _connection_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE OAuth connections (`_bound_connections`) resolved to
    {<PROVIDER>_ACCESS_TOKEN: token}, passed to run_util as extra_secrets. A util only sees a
    token it declares AND the run holds; a missing / needs-reauth binding is simply absent
    (the util then fails for want of a token).
    """
    bound = _bound_connections(ctx)
    if not bound:
        return {}
    env, warnings = oauth_store.tokens_for_routine(bound)
    for w in warnings:                       # a broken binding must not fail SILENTLY
        log.warning("connections: %s", w)
    return env

def _machine_env(ctx: RunContext, util: str) -> dict[str, str]:
    """The routine's EFFECTIVE remote machines (config bindings + one-time machine grants)
    resolved to RSCHED_MACHINES (connection metadata) + RSCHED_MACHINE_KEYS (private-key
    PEMs), passed to run_util as extra_secrets; only the reserved `remote` util declares them.
    An unresolvable binding is absent. A one-time grant covers EXEC only — mounts come with
    forever-bindings, made by the daemon.

    While the root command check is on, a root machine's key reaches only a DIRECT `remote`
    call (`util`), the one call it sees (engine/rootcheck.py); a script or util that
    `calls: remote` gets the machine's metadata without it.
    """
    from ..machine_privilege import is_root
    from .rootcheck import run_mode

    bound = list(ctx.routine.machines or [])
    bound += [eid.partition(":")[2] for eid in sorted(ctx.granted_now)
              if eid.startswith("machine:") and eid.partition(":")[2] not in bound]
    if not bound:
        return {}
    env, warnings = machines.machines_for_routine(bound, ctx.server.machines)
    for w in warnings:                       # a broken binding must not fail SILENTLY
        log.warning("machines: %s", w)
    if util != REMOTE_UTIL and run_mode(ctx) != "off":
        keys = json.loads(env[machines.MACHINE_KEYS_VAR])
        env[machines.MACHINE_KEYS_VAR] = json.dumps(
            {name: pem for name, pem in keys.items() if not is_root(ctx.server, name)},
            separators=(",", ":"))
    return env

def _routine_secrets(ctx: RunContext) -> dict[str, str]:
    """This routine's own scoped store (D103). A conversation or background task has a slug
    too, so the same mechanism serves them; a routine with no store contributes nothing.
    """
    from ..secrets import load_routine_secrets

    try:
        return load_routine_secrets(ctx.routine.slug)
    except ValueError:
        return {}     # a dir-path routine whose name is not a slug has no scoped store

def _llm_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE inference endpoints resolved to RSCHED_LLM (endpoint metadata +
    the role map) + RSCHED_LLM_KEYS (api keys for the role endpoints only), passed to run_util
    as extra_secrets. Only a util that DECLARES them receives them, which is what makes shipping
    a key here safe (endpoints/routine_env.py holds the design).

    Read with `getattr`: `_extra_secrets` runs before EVERY util call, and some callers
    (`mediaops`' vision fallback, the reserved-token path) pass context stand-ins without these
    attributes. An optional env var must fail OPEN by contributing nothing, never raise.
    """
    from ..endpoints.routine_env import llm_env_for_routine

    models = getattr(ctx.routine, "models", None) or {}
    if not hasattr(ctx.server, "endpoints"):
        return {}
    return llm_env_for_routine(ctx.routine.slug, models, ctx.server)

def _decision_env(ctx: RunContext) -> dict[str, str]:
    """The routine's effective DECISION catalog resolved to RSCHED_DECISIONS (catalog metadata +
    the role map + each endpoint's PROTOCOL) and RSCHED_DECISIONS_KEYS (keys for the role
    endpoints only), passed to run_util as extra_secrets; only a declaring util receives them.
    The decision counterpart of `_llm_env`, read with `getattr`/`hasattr` for the same reason.
    """
    from ..endpoints.routine_env import decision_env_for_routine

    models = getattr(ctx.routine, "models", None) or {}
    if not hasattr(ctx.server, "decision_endpoints"):
        return {}
    return decision_env_for_routine(ctx.routine.slug, models, ctx.server)

def _extra_secrets(ctx: RunContext, util: str = "") -> dict[str, str]:
    """Engine-resolved, per-run secrets a util may receive (still under the declared-only gate):
    the routine's OWN scoped secrets, OAuth connection access tokens, bound remote-machine
    details/keys, the routine's own inference endpoints and its DECISION catalog. The var names
    are disjoint, so a plain merge is safe. extra_secrets WIN the `_child_env` merge.

    ROUTINE-SCOPED SECRETS (D103): a routine's own `secrets.d/<slug>.env` value shadows a
    central one of the same name, for its runs only; no grant is checked.

    RSCHED_ROUTINE: WHO is calling — unforgeable by the caller, so a util can scope itself to
    the routine (the `ftp` util resolves `{routine}` with it).

    RSCHED_LLM / RSCHED_LLM_KEYS: WHERE this routine's inference goes, so a util honours a
    routine configured for a local model. Keys cover ROLE endpoints only; any other key stays
    behind its own `secret:<NAME>` grant.

    `util` names the util a DIRECT util call runs; a script's or any other caller's is "", so a
    root machine's key is left out while the root command check is on (`_machine_env`).

    RSCHED_API_TOKEN: always the server's read-only ROUTINE token, UNCONDITIONALLY overriding
    any store value so the primary console token can never reach a util. An empty routine token
    is injected as "" (the call fails visibly with a 401) — never skipped, which would let the
    store's value through.
    """
    out = {**_routine_secrets(ctx), **_connection_env(ctx), **_machine_env(ctx, util),
           **_llm_env(ctx), **_decision_env(ctx)}
    out["RSCHED_API_TOKEN"] = str(ctx.server.routine_token or "")
    out["RSCHED_ROUTINE"] = ctx.routine.slug
    return out

def _unbound_connection_request(ctx: RunContext, name: str) -> str:
    """The one-click repair route for a util that failed for want of an unbound connection.

    A connection is a grant entity (`connection:<provider>`), so the run is routed to request
    it rather than narrate it. A connection granted for THIS run counts as bound. Returns the
    route sentence, or "" when nothing is missing.
    """
    from ..oauth.providers import access_token_var, provider_ids

    declared = utils_run.util_needs(ctx.server.libraries_home, name).secrets
    upper = {d.upper() for d in declared}
    bound = _bound_connections(ctx)
    wanted = [pid for pid in provider_ids() if access_token_var(pid) in upper]
    missing = [pid for pid in wanted if not bound.get(pid)]
    # A util may serve several connections; when one IS bound, the call had a token and failed
    # for another reason, so no request is routed.
    if not missing or len(missing) < len(wanted):
        return ""
    eid = f"connection:{missing[0]}"
    return (f'This util needs a bound {missing[0]} connection — it declares '
            f'{access_token_var(missing[0])}, which the engine injects only from a binding, '
            f'and this routine has none. That is a grantable entity: ask for it with '
            f'{{"kind": "ask_user", "request": "{eid}", "question": "<why you need it>"}} '
            f'and the user can allow it in one click on the Decisions page. Do NOT explain '
            f'the missing binding in prose and move on. ')
