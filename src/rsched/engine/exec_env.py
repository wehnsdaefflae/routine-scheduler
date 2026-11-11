"""What a util subprocess is ALLOWED TO SEE — the environment assembled for one call.

Split out of `executor.py` (F393): dispatching an action is one job, deciding which credentials
cross into a subprocess is another, and the second is the security boundary.

Everything here is declared-only by construction. A connection token reaches a util iff the
routine binds the provider AND the util declares the var; a machine key iff the routine binds
the machine; a routine-scoped secret shadows a central one of the same name (D103). An
unbound connection becomes an access REQUEST rather than a silent empty string, because a util
that runs with no token fails in a way nobody can read.
"""

from __future__ import annotations

import logging

from .. import machines, utils_run
from ..oauth import store as oauth_store
from .run_context import RunContext

log = logging.getLogger("rsched.engine.exec_env")


def _bound_connections(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE OAuth bindings, {provider: account}: the config bindings plus
    this run's one-time connection grants (the decision recorded the account in
    ctx.grant_args). The one reading of "does this run hold a connection", so the token a util
    receives and the request a failed util is routed to cannot disagree about it.
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

def _machine_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE remote machines (config bindings + one-time machine grants)
    resolved to RSCHED_MACHINES (connection metadata) + RSCHED_MACHINE_KEYS (private-key
    PEMs from the Secrets store), passed to run_util as extra_secrets. Only the reserved
    `remote` util declares these, so only it receives them; an unresolvable binding
    (missing catalog entry / unset key) is simply absent from the maps. A one-time grant
    covers EXEC only — the sshfs share is mounted by the daemon at binding time, so
    mounts come with forever-bindings.
    """
    bound = list(ctx.routine.machines or [])
    bound += [eid.partition(":")[2] for eid in sorted(ctx.granted_now)
              if eid.startswith("machine:") and eid.partition(":")[2] not in bound]
    if not bound:
        return {}
    env, warnings = machines.machines_for_routine(bound, ctx.server.machines)
    for w in warnings:                       # a broken binding must not fail SILENTLY
        log.warning("machines: %s", w)
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
    as extra_secrets. Only a util that DECLARES them receives them — which is what makes
    shipping a key through this channel safe at all. See endpoints/routine_env.py for the whole
    design, including why keys cover the role endpoints and not every configured one.

    Read with `getattr`, and that is not timidity. `_extra_secrets` runs before EVERY util
    call, including `mediaops`' vision fallback and the engine's own reserved-token path, and
    those callers hand in context stand-ins that carry what their own subject needs and nothing
    more. A hard attribute read here does not surface a config fault — a real `RoutineConfig`
    and `ServerConfig` always carry these keys — it turns an OPTIONAL env var into an
    AttributeError that kills the util call that was going to work. Measured: the first version
    read `ctx.routine.models` directly and redded 8 tests in 6 files on this exact path
    (`test_api`, `test_utils` ×2, `test_view_image` ×2, `test_abort_ends_command`,
    `test_cancel_b2_intent_channel`, `test_secret_decline_withholds`) — none of them about
    endpoints. The same reasoning the boot migrations use for `server.conversations_home`: a
    helper on a path this hot fails OPEN, by contributing nothing.
    """
    from ..endpoints.routine_env import llm_env_for_routine

    models = getattr(ctx.routine, "models", None) or {}
    if not hasattr(ctx.server, "endpoints"):
        return {}
    return llm_env_for_routine(ctx.routine.slug, models, ctx.server)

def _extra_secrets(ctx: RunContext) -> dict[str, str]:
    """Engine-resolved, per-run secrets a util may receive (still under the declared-only gate):
    the routine's OWN scoped secrets, OAuth connection access tokens, bound remote-machine
    details/keys, and the routine's own inference endpoints. The var names are disjoint, so a
    plain merge is safe.

    ROUTINE-SCOPED SECRETS (D103): `secrets.d/<slug>.env` rides this channel because
    extra_secrets WIN the _child_env merge — so a routine's own `SFTP_USER` shadows a central
    value of the same name for its runs, and reaches no other routine. There is no grant to
    check: a scoped secret is the routine's own, implicitly exposed to it (secrets.py).

    RSCHED_ROUTINE: WHO is calling, which is the one thing a util cannot be told by its own
    caller. A routine cannot forge its environment, so a util handed this can scope itself to
    the caller instead of to an argument — the `ftp` util resolves `{routine}` in a source's
    base directory with it, which is what lets one shared credential serve many routines with
    nothing configured per routine. Injected for every run and, like every other name here,
    delivered only to a util that DECLARES it.

    RSCHED_LLM / RSCHED_LLM_KEYS (F868): WHERE this routine's inference goes, which is the
    other thing a util cannot be told by its own caller. Every LLM-calling util hardcoded a
    provider, because nothing carried the routine's effective endpoint — so a routine
    configured for a LOCAL model still sent its prompt to a cloud one, and for a run reading
    private files that is a privacy breach rather than a cost. Exactly the RSCHED_ROUTINE
    argument, about the destination instead of the caller: engine knowledge, unforgeable by
    the caller, useless to a util that does not ask for it. The keys ride the same channel
    because the declared-only gate is what makes that safe, and they cover the routine's ROLE
    endpoints only — a key for any other endpoint stays behind its own `secret:<NAME>` grant
    row, which is the operator's decision and not a run's. endpoints/routine_env.py holds the
    whole design.

    RSCHED_API_TOKEN (R94, operator decision 2026-08-05: ENFORCE): the reserved name a
    util declares to talk to the daemon API resolves to the server's ROUTINE token — the
    read-only tier — and OVERRIDES any secrets-store value for it (extra_secrets win the
    _child_env merge by design), so the primary console token can never reach a util
    subprocess through the store. The override is UNCONDITIONAL, and that is the whole of
    the promise: it used to be written `if routine_token:`, so a blank or deleted
    `routine_token:` made the override vanish and `scoped_env` then injected whatever the
    central store held under that name — which on this instance was byte-for-byte the
    PRIMARY console token. A guard that fails open on the empty value is not a seal. An
    empty routine token now reaches the util as an empty string and the call fails visibly
    against a 401, which is the behaviour an operator can act on. Config stays honest: the
    engine reads `routine_token` here, it never writes it (bootstrap.ensure_config
    generates it).
    """
    out = {**_routine_secrets(ctx), **_connection_env(ctx), **_machine_env(ctx),
           **_llm_env(ctx)}
    out["RSCHED_API_TOKEN"] = str(ctx.server.routine_token or "")
    out["RSCHED_ROUTINE"] = ctx.routine.slug
    return out

def _unbound_connection_request(ctx: RunContext, name: str) -> str:
    """F321 (from R333): the one-click repair route for a util that failed because a
    connection it needs is not bound to this routine.

    `google-api` failing with "$GOOGLE_ACCESS_TOKEN is not set" used to be explained in
    PROSE in the finish summary, while a missing fs-write root in the same conversation
    correctly produced a typed access request the user could approve inline. The asymmetry
    was the whole complaint: a connection IS a grant entity (`connection:<provider>`,
    entities.py), so a run should be routed to request it, not to narrate it. A connection
    granted for THIS run counts as bound — it was injected, so the failure lies elsewhere and
    a request for it would ask the user again for what they just allowed.

    Returns the route sentence, or "" when nothing is missing.
    """
    from ..oauth.providers import access_token_var, provider_ids

    declared = utils_run.util_needs(ctx.server.libraries_home, name).secrets
    upper = {d.upper() for d in declared}
    bound = _bound_connections(ctx)
    wanted = [pid for pid in provider_ids() if access_token_var(pid) in upper]
    missing = [pid for pid in wanted if not bound.get(pid)]
    # A util may serve several connections (google-api: Google and Google Health). When one of
    # them IS bound, the call had a token and failed for another reason — routing it to request
    # the other would send every Drive 403 to ask for a Health connection.
    if not missing or len(missing) < len(wanted):
        return ""
    eid = f"connection:{missing[0]}"
    return (f'This util needs a bound {missing[0]} connection — it declares '
            f'{access_token_var(missing[0])}, which the engine injects only from a binding, '
            f'and this routine has none. That is a grantable entity: ask for it with '
            f'{{"kind": "ask_user", "request": "{eid}", "question": "<why you need it>"}} '
            f'and the user can allow it in one click on the Decisions page. Do NOT explain '
            f'the missing binding in prose and move on. ')
