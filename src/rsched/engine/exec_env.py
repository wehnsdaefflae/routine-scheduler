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


def _connection_env(ctx: RunContext) -> dict[str, str]:
    """The routine's EFFECTIVE OAuth connections resolved to {<PROVIDER>_ACCESS_TOKEN: token},
    passed to run_util as extra_secrets: the config bindings plus this run's one-time
    connection grants (the decision recorded the account in ctx.grant_args). A util only
    sees a token it declares AND the run holds; a missing / needs-reauth binding is simply
    absent (the util then fails for want of a token).
    """
    bound = dict(ctx.routine.connections or {})
    for eid in sorted(ctx.granted_now):
        if eid.startswith("connection:"):
            provider = eid.partition(":")[2]
            bound.setdefault(provider, str(ctx.grant_args.get(eid) or ""))
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

def _extra_secrets(ctx: RunContext) -> dict[str, str]:
    """Engine-resolved, per-run secrets a util may receive (still under the declared-only gate):
    the routine's OWN scoped secrets, OAuth connection access tokens, and bound remote-machine
    details/keys. The var names are disjoint, so a plain merge is safe.

    ROUTINE-SCOPED SECRETS (D103): `secrets.d/<slug>.env` rides this channel because
    extra_secrets WIN the _child_env merge — so a routine's own `SFTP_USER` shadows a central
    value of the same name for its runs, and reaches no other routine. There is no grant to
    check: a scoped secret is the routine's own, implicitly exposed to it (secrets.py).

    RSCHED_API_TOKEN (R94, operator decision 2026-08-05: ENFORCE): the reserved name a
    util declares to talk to the daemon API resolves to the server's ROUTINE token — the
    read-only tier — and OVERRIDES any secrets-store value for it (extra_secrets win the
    _child_env merge by design), so the primary console token can never reach a util
    subprocess through the store. Config stays honest: the engine reads `routine_token`
    here, it never writes it (bootstrap.ensure_config generates it).
    """
    out = {**_routine_secrets(ctx), **_connection_env(ctx), **_machine_env(ctx)}
    routine_token = str(getattr(ctx.server, "routine_token", "") or "")
    if routine_token:
        out["RSCHED_API_TOKEN"] = routine_token
    return out

def _unbound_connection_request(ctx: RunContext, name: str) -> str:
    """F321 (from R333): the one-click repair route for a util that failed because a
    connection it needs is not bound to this routine.

    `google-api` failing with "$GOOGLE_ACCESS_TOKEN is not set" used to be explained in
    PROSE in the finish summary, while a missing fs-write root in the same conversation
    correctly produced a typed access request the user could approve inline. The asymmetry
    was the whole complaint: a connection IS a grant entity (`connection:<provider>`,
    entities.py), so a run should be routed to request it, not to narrate it.

    Returns the route sentence, or "" when nothing is missing.
    """
    from ..oauth.providers import access_token_var, provider_ids

    declared = utils_run.util_needs(ctx.server.libraries_home, name).secrets
    upper = {d.upper() for d in declared}
    bound = dict(getattr(ctx.routine, "connections", None) or {})
    missing = [pid for pid in provider_ids()
               if access_token_var(pid) in upper and not bound.get(pid)]
    if not missing:
        return ""
    eid = f"connection:{missing[0]}"
    return (f'This util needs a bound {missing[0]} connection — it declares '
            f'{access_token_var(missing[0])}, which the engine injects only from a binding, '
            f'and this routine has none. That is a grantable entity: ask for it with '
            f'{{"kind": "ask_user", "request": "{eid}", "question": "<why you need it>"}} '
            f'and the user can allow it in one click on the Decisions page. Do NOT explain '
            f'the missing binding in prose and move on. ')
