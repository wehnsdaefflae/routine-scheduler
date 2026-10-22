"""What a UTIL GRANT carries: the secrets the granted util itself declares (F562).

Granting `util:<name>` used to leave every secret that util declares `undecided`, so the
routine's first call ran straight into the exposure gate (`secretgate`) and filed a SECOND
blocking access request for the same capability. `bkm-foerder-steward:20260925-062318` did
exactly that — granted a util, blocked again on the util's own credential — and R2106
(`suedlink-wlf`) is the same shape from the permissions side. A granted util was half-usable,
and the operator paid two clicks for one decision.

The fix WIDENS THE REQUEST, never the interpretation: when `util:<name>` is asked for, the
access request ALSO carries `secret:<NAME>` for each secret that util declares, so one
operator decision covers both and every exposure is still an explicit grant row. The cheaper
alternative — teaching `secretgate.secret_state` to answer `granted` whenever some held
`util:` grant happens to declare the name — is fewer lines and worse: a util grant would
SILENTLY carry credentials, and `secret_state` would be answering about rows that do not
exist (the reading R2702 cautioned against for config — enforce at the boundary, never infer
from an adjacent grant). `secret_state` stays honest: it answers about rows, and this module
makes sure the rows get asked for.

Three properties keep the widening from being a back door:

- VERB-BOUNDED (R2622). A `NAME@verb` secret rides only with a grant that REACHES that verb:
  `util:signal:read` carries nothing bound to `send`, while a bare `util:signal` — which
  grants every verb — carries both. An UNBOUND name rides with any grant of its util, which
  is exactly what the util's own calls receive.
- ONLY WHAT AN EXPOSURE DECISION IS ABOUT. `secretgate._exposable` is the single judge, so
  this module cannot drift from the gate it is feeding: REQUIRED names only (an OPTIONAL `?`
  one is withheld silently and never worth a decision, D51/F290), present in the CENTRAL
  store, and not shadowed by the routine's own scoped store (D103).
- ONLY UNDECIDED NAMES. An allow on a util never re-opens a tombstoned secret, and a name
  already granted is not put to the operator twice.

And it is VISIBLE. The Decisions card renders the question's PROSE, not the entity ids
(`static/components/answerform.js` reads `q.request` only to pick which buttons to offer), so
`note()` names the carried secrets in the question itself: a click that exposes a credential
has to say so, or the widening would be the silent carry it was built to avoid.

Every import here is deliberately IN-FUNCTION. `secretgate` imports `interact` at module
level and `interact` imports this module, so a module-level `from . import secretgate` would
be a cycle resolved at exactly the wrong moment (interact's import runs before `handle_ask`
is defined).
"""

from __future__ import annotations


def carried(ctx, entry: str) -> list[str]:
    """The secret names a grant of the util entity `<name>[:<verb>]` should carry with it.

    `entry` is the entity id's NAME part, so both grains the vocabulary allows are read here:
    a bare util (every verb) and a `name:verb` scope (that one subcommand, `grants.py`).
    Declarations are resolved over the whole `calls:` tree (`utils_run.util_needs`), because
    one call tree runs inside one env — a credential a callee declares is one the caller's
    subprocess receives, so it is part of what this grant will really expose.
    """
    from .. import utils_lib, utils_run
    from ..grants import split_util_verb
    from ..utils_verbs import verb_scope
    from . import secretgate

    home = ctx.server.libraries_home
    bare, verb = split_util_verb(entry)
    if not utils_lib.exists(home, bare):
        return []                      # nothing is declared by a util that does not exist
    needs = utils_run.util_needs(home, bare)
    scope = verb_scope(home, bare)
    out: list[str] = []
    for name in secretgate._exposable(ctx, needs.secrets, needs.optional):
        bound = scope.get(name) or set()
        if bound and verb and verb not in bound:
            continue                   # bound to other verbs only — this scope never sees it
        if secretgate.secret_state(ctx, name) == "undecided":
            out.append(name)
    return out


def note(question: str, names: list[str]) -> str:
    """The question an operator is owed when the decision carries credentials as well.

    Appended to the run's own prose rather than replacing it: WHY the entity is needed is the
    run's to say, WHAT the click exposes is the engine's, and the card shows the prose.
    """
    if not names:
        return question
    plural = len(names) > 1
    return (f"{question.rstrip()}\n\n[This grant also exposes the util's own declared "
            f"secret{'s' if plural else ''} to this routine: {', '.join(names)} — allowing "
            f"the util allows {'them' if plural else 'it'}.]")


def widen(ctx, ids: list[str], question: str) -> tuple[list[str], str]:
    """`(the entity ids this access request should really carry, the question that says so)`.

    One pass over the ids a request names: every `util:` id contributes the secrets that util
    declares, de-duplicated and in declaration-free sorted order (`carried`), and the question
    gains the line naming them. Ids of every other class pass through untouched, and a request
    that carries nothing new is returned exactly as it came — a plain ask is not touched at all.
    """
    extra: list[str] = []
    for rid in ids:
        if not rid.startswith("util:"):
            continue
        extra += [n for n in carried(ctx, rid.partition(":")[2])
                  if f"secret:{n}" not in ids and n not in extra]
    if not extra:
        return ids, question
    return [*ids, *(f"secret:{n}" for n in extra)], note(question, extra)
