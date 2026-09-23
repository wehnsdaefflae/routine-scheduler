"""The RESERVED-UTIL gate — may this run call this util, and everything that util calls?

Split out of `grantpolicy.py` when the gate grew its second question. The first is the one the
capability model advertises: is the NAME the model typed reserved, and does the routine hold it
(by name, by tag class, or by verb). The second is the one it did not enforce: a util's
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
    """The reserved-util gate. A util is granted BY NAME (`capabilities.utils`), BY TAG
    CLASS (`util_tags` — covers every util in the class, including ones the library gains
    later), or BY VERB (`name:verb` — that one subcommand, matched against the call's
    first positional argument, which is how read-only access to a channel is expressed).

    Granted by any of those three, the call still meets `_deny_call_tree`: what a util
    `calls:` is part of what calling it grants.
    """
    name = str(action.get("name") or "")
    if policy.admin:
        return None
    if name not in policy.gated_utils or name in policy.utils:
        return _deny_call_tree(policy, name)
    if set(policy.util_tag_index.get(name, ())) & policy.util_tags:
        return _deny_call_tree(policy, name)
    args = action.get("args") or []
    verb = str(args[0]) if args and isinstance(args[0], str) else ""
    scoped = {v for u in policy.utils
              if (n := split_util_verb(u))[0] == name and (v := n[1])}
    if scoped:
        if verb in scoped:
            return _deny_call_tree(policy, name)
        miss = f"{verb!r} is not one of those" if verb else "this call names no verb"
        return (f"util {name!r} is granted to this routine only for: "
                f"{', '.join(sorted(scoped))}. {miss} — a read-only channel is not a "
                f"write one. {policy.request_route(f'util:{name}')}")
    perms = ", ".join(policy.gated_utils[name])
    return (f"util {name!r} is a reserved capability switched OFF for this "
            f"routine — this channel is off limits (the {perms} permission "
            f"covers its conduct). {policy.request_route(f'util:{name}')}")

def _deny_call_tree(policy: GrantPolicy, name: str) -> str | None:
    """The same gate over the call's `calls:` TREE: a util's declaration is a self-service
    grant of what it names, since `util_needs` folds every callee's credentials and jail
    into the caller's and the library root is on PATH for every util.

    An edge is refused for what it CONFERS — the callee carries credentials, or the caller
    really execs it — never for merely existing, and the refusal names the EDGE, because a
    denial reading "remote is off" for a call to `rephrase-as-human` is unactionable. See
    docs/rules-permissions.md § The gate follows `calls:` too.
    """
    if policy.libraries_home is None or not policy.gated_utils:
        return None
    from .utils_run import util_needs

    for reached in util_needs(policy.libraries_home, name).tree:
        if reached == name or reached not in policy.gated_utils:
            continue
        if reached in policy.utils or (set(policy.util_tag_index.get(reached, ()))
                                     & policy.util_tags):
            continue
        confers = ("its credentials" if util_needs(policy.libraries_home, reached).secrets
                   else "a way to run it" if _execs(policy, name, reached) else "")
        if not confers:
            continue
        perms = ", ".join(policy.gated_utils[reached])
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
