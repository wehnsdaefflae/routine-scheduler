"""The RESERVED-UTIL gate — may this run call this util, and everything that util calls?

Split out of `grantpolicy.py` when the gate grew its second question. The first is the one the
capability model advertises: is the NAME the model typed reserved, and does the routine hold it
(by name, or by verb). The second is the one it did not enforce: a util's
docstring `calls:` line folds every callee's `secrets:`, `net:` and `fs:` into the caller's one
jail and one env, and the library root is on PATH for every util — so naming a reserved sibling
is a second, unaudited door into that channel.

`GrantPolicy` stays the object every gate asks; this is one of its answers, kept apart because
it is the only one that reads the util LIBRARY rather than the routine's config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .grants import split_util_verb

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .grantpolicy import GrantPolicy


def deny_util(policy: GrantPolicy, action: dict) -> str | None:
    """The reserved-util gate. A doc reserves a util WHOLE (a bare name) or VERB BY VERB
    (`name:verb` — the call's first positional argument). A routine is granted a reserved util
    by name (every verb) or by `name:verb` (that verb only).

    A util reserved only verb by verb is open for every other verb: reading a mailbox is
    gated by its credential; only sending needs the permission. Granted by either route,
    the call still meets `_deny_call_tree`: what a util `calls:` is part of what calling it
    grants.
    """
    name = str(action.get("name") or "")
    if policy.admin:
        return None
    args = action.get("args") or []
    verb = str(args[0]) if args and isinstance(args[0], str) else ""
    if name in policy.gated_utils:
        if name in policy.utils:
            return _deny_call_tree(policy, name)
        scoped = {v for u in policy.utils
                  if (n := split_util_verb(u))[0] == name and (v := n[1])}
        if scoped:
            if verb in scoped:
                return _deny_call_tree(policy, name)
            miss = f"{verb!r} is not one of those" if verb else "this call names no verb"
            return (f"util {name!r} is granted to this routine only for: "
                    f"{', '.join(sorted(scoped))}. {miss}. "
                    f"{policy.request_route(f'util:{name}')}")
        perms = ", ".join(policy.gated_utils[name])
        return (f"util {name!r} is a reserved capability switched OFF for this "
                f"routine — this channel is off limits (the {perms} permission "
                f"covers its conduct). {policy.request_route(f'util:{name}')}")
    reserved = policy.gated_verbs.get(name, {})
    if verb in reserved and name not in policy.utils and f"{name}:{verb}" not in policy.utils:
        perms = ", ".join(reserved[verb])
        return (f"`{name} {verb}` acts outward and is switched OFF for this routine — the "
                f"{perms} permission covers it (reading with this util needs no permission). "
                f"{policy.request_route(f'util:{name}')}")
    return _deny_call_tree(policy, name)


def _deny_call_tree(policy: GrantPolicy, name: str) -> str | None:
    """The same gate over the call's `calls:` TREE: a util's declaration is a self-service
    grant of what it names, since `util_needs` folds every callee's credentials and jail
    into the caller's and the library root is on PATH for every util.

    An edge is refused for what it CONFERS — the callee carries credentials, or the caller
    really execs it — never for merely existing, and the refusal names the EDGE, because a
    denial reading "remote is off" for a call to `rephrase-as-human` is unactionable. See
    docs/rules-permissions.md § The gate follows `calls:` too.
    """
    if policy.libraries_home is None or not (policy.gated_utils or policy.gated_verbs):
        return None
    from .utils_run import util_needs

    for reached in util_needs(policy.libraries_home, name).tree:
        if reached == name or not (reached in policy.gated_utils
                                   or reached in policy.gated_verbs):
            continue
        if reached in policy.utils or any(split_util_verb(u)[0] == reached
                                          for u in policy.utils):
            continue
        confers = ("its credentials" if util_needs(policy.libraries_home, reached).secrets
                   else "a way to run it" if _execs(policy, name, reached) else "")
        if not confers:
            continue
        perms = ", ".join(policy.gated_utils.get(reached) or sorted(
            {d for docs in policy.gated_verbs.get(reached, {}).values() for d in docs}))
        return (f"util {name!r} declares `calls: {reached}`, and {reached!r} is a "
                f"reserved capability switched OFF for this routine — the edge hands the "
                f"caller {confers}, so it is the same channel by another name (the "
                f"{perms} permission covers its conduct). "
                f"{policy.request_route(f'util:{reached}')}")
    return None

def _execs(policy: GrantPolicy, caller: str, callee: str) -> bool:
    """Does `caller`'s source shell out to `gu <callee>`? Declaring a sibling and running
    it are different acts (the same evidence `scripts.call_problems` reads).
    """
    from .utils_header import GU_CALL_RE
    from .utils_lib import read_util

    src = read_util(policy.libraries_home, caller) if policy.libraries_home else None
    return src is not None and callee in GU_CALL_RE.findall(src)
