"""What a UTIL GRANT carries: the secrets the granted util itself declares (F562).

Without this, granting `util:<name>` left the util's own secrets `undecided`, so its first
call filed a SECOND blocking request for the same capability. The fix WIDENS THE REQUEST, never
the interpretation: an access request for `util:<name>` ALSO carries `secret:<NAME>` for each
secret that util declares, so one decision covers both and every exposure is still an explicit
grant row. `secretgate.secret_state` keeps answering about rows only; inferring exposure from an
adjacent util grant would make that grant SILENTLY carry credentials.

Three properties keep the widening from being a back door:

- VERB-BOUNDED (R2622). A `NAME@verb` secret rides only with a grant that REACHES that verb:
  `util:signal:read` carries nothing bound to `send`, while a bare `util:signal` carries both.
  An UNBOUND name rides with any grant of its util.
- ONLY WHAT AN EXPOSURE DECISION IS ABOUT. `secretgate._exposable` is the single judge:
  REQUIRED names only, present in the CENTRAL store, not shadowed by the routine's own store.
- ONLY UNDECIDED NAMES. An allow on a util never re-opens a tombstoned secret, and a name
  already granted is not asked twice.

And it is VISIBLE: the Decisions card renders the question's PROSE, not the entity ids, so
`note()` names the carried secrets in the question itself.

Every import here is IN-FUNCTION: `secretgate` imports `interact`, which imports this module,
so a module-level import would be a cycle.
"""

from __future__ import annotations


def carried(ctx, entry: str) -> list[str]:
    """The secret names a grant of the util entity `<name>[:<verb>]` should carry with it.

    `entry` is the entity id's NAME part: a bare util (every verb) or a `name:verb` scope.
    Declarations are resolved over the whole `calls:` tree, since one call tree runs inside
    one env.
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

    Every `util:` id contributes the secrets that util declares (de-duplicated, `carried`) and
    the question gains the line naming them. Other ids pass through, and a request carrying
    nothing new is returned exactly as it came.
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
