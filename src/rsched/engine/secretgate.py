"""The SECRET-EXPOSURE gate — the approval a util or script needs before it sees a
credential.

One question — may THIS call see THESE secrets — answered from the routine's four-state grant
standing (`grants:` rows are the forever states, the run overlay the once states) and, when
undecided, by asking the user through the ordinary decision record.

An OPTIONAL (`?`-declared) secret never blocks: it is withheld and the run is told what it lost.

A DECLINED REQUIRED secret is withheld the same way and the call proceeds: a decline protects
the credential, not the util — forbidding a util is a `util:` grant's job. This gate only sees
calls the RUN chose (`util`, a `/util` slash command, a routine script). The engine's own
credentialed call — the `view_image` vision fallback — REFUSES on a decline instead. That
asymmetry is deliberate (D213): the engine refuses for calls IT chose to make and withholds
for calls the RUN chose, so a decline never silently degrades work the run did not ask for.

`secret_state` answers about ROWS only, never about an adjacent `util:` grant declaring the
name; a util grant's secrets are carried by widening the REQUEST (`engine/grantsecrets.py`),
so a util grant never silently carries credentials.
"""

from __future__ import annotations

from typing import NamedTuple

from .. import utils_lib, utils_run
from .actionschema import PSEUDO_UTILS
from .askback import still_pending
from .interact import handle_ask


def secret_state(ctx, secret: str) -> str:
    """One secret's four-state grant standing for this run: granted | denied | undecided.
    `grants:` rows are the forever states, the run overlay (granted_now/denied_now) the
    once states.
    """
    eid = f"secret:{secret}"
    grants = dict(ctx.routine.grants or {})
    if eid in ctx.granted_now:
        return "granted"
    if eid in ctx.denied_now or grants.get(eid) is False:
        return "denied"
    return "granted" if grants.get(eid) is True else "undecided"


def _own_secrets(ctx) -> set[str]:
    """Names in the routine's OWN scoped store (D103). They are implicitly exposed to their
    owner — no grant, no ask — and they SHADOW a central value of the same name, so every
    exposure decision about the central store must skip them or it asks about a value this
    run will never be handed.
    """
    from ..secrets import load_routine_secrets

    try:
        return set(load_routine_secrets(ctx.routine.slug))
    except ValueError:
        return set()


def _exposable(ctx, needed: set[str], optional: set[str]) -> list[str]:
    """The names an exposure decision is about: the REQUIRED ones a call declares that the
    central store holds and the routine's own store does not shadow (D103). A declared name
    the store lacks needs no decision — the call fails visibly inside instead.
    """
    from ..secrets import load_secrets

    required = needed - optional
    return sorted((required & set(load_secrets())) - _own_secrets(ctx)) if required else []


def declined_secrets(ctx, name: str) -> list[str]:
    """The exposable secrets util `name` declares that the user DECLINED for this routine.

    For a call the ENGINE makes on the run's behalf (the view_image vision fallback): it asks
    nothing, but must still honour a decline already given.
    """
    needs = utils_run.util_needs(ctx.server.libraries_home, name)
    return [s for s in _exposable(ctx, needs.secrets, needs.optional)
            if secret_state(ctx, s) == "denied"]


def withheld_optional(ctx, optional: set[str]) -> list[str]:
    """The OPTIONAL (`?`-declared) secrets present in the store that this run may NOT see.
    They never block or ask: the executor withholds them from the child env and the
    observation says so.
    """
    from ..secrets import load_secrets
    own = _own_secrets(ctx)
    return sorted(s for s in (optional & set(load_secrets())) - own
                  if secret_state(ctx, s) != "granted")


class Withheld(NamedTuple):
    """What a call that RUNS does not get of the secrets it declares, by the two reasons a
    declared credential may be absent while the call still proceeds:

    * `optional` — a `?`-declared name the routine may not see. Never blocks, never asks;
      undecided ones are requestable, so they may be named.
    * `declined` — a REQUIRED name the user DECLINED; withheld and the call runs without it.
      Only a count reaches the model: a denial enumerates nothing (R17).
    """

    optional: tuple[str, ...] = ()
    declined: tuple[str, ...] = ()

    @property
    def names(self) -> set[str]:
        """Every name to scrub from the child env — `utils_run.scoped_env`'s `withhold`."""
        return {*self.optional, *self.declined}

    def note(self, ctx) -> dict:
        """The observation fields for both reasons; empty when the call got everything."""
        out: dict = {}
        if self.optional:
            undecided = [s for s in self.optional if secret_state(ctx, s) == "undecided"]
            out["withheld_optional"] = {"undecided": undecided,
                                        "denied": len(self.optional) - len(undecided)}
        if self.declined:
            out["withheld_declined"] = len(self.declined)       # R17: a count, never names
        return out


def withheld_for_call(ctx, needed: set[str], optional: set[str]) -> Withheld:
    """Both withholdings for one call's transitive declarations. The declined half reads the
    same `secret_state` as the gate, so a `grants:` decline and one given at the ask agree.
    """
    return Withheld(tuple(withheld_optional(ctx, optional)),
                    tuple(s for s in _exposable(ctx, needed, optional)
                          if secret_state(ctx, s) == "denied"))


def withheld_for_util(ctx, name: str) -> Withheld:
    """`withheld_for_call` over a UTIL's transitive declarations."""
    home = ctx.server.libraries_home
    if not utils_lib.exists(home, name):
        return Withheld()
    needs = utils_run.util_needs(home, name)
    return withheld_for_call(ctx, needs.secrets, needs.optional)


def withheld_verb_scoped(ctx, name: str, args: list) -> dict:
    """What a VERB-SCOPED declaration withheld from THIS call, as the observation field —
    `{"verb": <the verb that ran, or None>, "secrets": [names]}`, empty when the call tree
    binds no secret to a verb.

    Reported because, inside the util, a withheld secret looks exactly like an unprovisioned
    one. A call naming NO verb (default path, `--selftest`) withholds every bound name.
    """
    from ..utils_verbs import withheld_for_verb
    home = ctx.server.libraries_home
    if not utils_lib.exists(home, name):
        return {}
    withheld, verb = withheld_for_verb(home, name, [str(a) for a in (args or [])])
    return {"withheld_verb_scoped": {"verb": verb, "secrets": withheld}} if withheld else {}


def withheld_optional_secrets(ctx, name: str) -> list[str]:
    """`withheld_optional` over a UTIL's transitive declarations."""
    home = ctx.server.libraries_home
    if not utils_lib.exists(home, name):
        return []
    optional = utils_run.util_needs(home, name).optional
    return withheld_optional(ctx, optional)


def gate_util_secrets(loop, action: dict, poll_s: float) -> dict | None:
    """Per-routine secret exposure (D39), decided at CALL time through the four-state grant
    model. A util call whose transitive `secrets:` name secrets PRESENT in the store runs
    only once this routine may see them; undecided names file ONE blocking access request
    covering all of them. Returns None to let the call proceed, or the refusing/pending
    observation. OPTIONAL secrets never reach the ask: they are withheld instead.
    """
    ctx = loop.ctx
    name = str(action.get("name") or "")
    home = ctx.server.libraries_home
    if name in PSEUDO_UTILS or not utils_lib.exists(home, name):
        return None                     # discovery / missing-util paths expose no secrets
    needs = utils_run.util_needs(home, name)
    needed, optional = needs.secrets, needs.optional
    return _gate_secrets(loop, kind="util", name=name, needed=needed, optional=optional,
                         poll_s=poll_s)


def gate_script_secrets(loop, action: dict, poll_s: float) -> dict | None:
    """The SAME four-state exposure gate for a per-routine script — needs resolved over
    its own header AND the `calls:` utils it declares, since one jail and one env cover
    the whole call tree.
    """
    from .. import scripts
    from .executor import script_home
    ctx = loop.ctx
    name = str(action.get("name") or "")
    if (home := script_home(ctx, name)) is None:
        return None                     # the missing-script path exposes no secrets
    needed, _net, optional = scripts.needs(home, name, ctx.server.libraries_home)
    return _gate_secrets(loop, kind="script", name=name, needed=needed,
                         optional=optional, poll_s=poll_s)


def _gate_secrets(loop, *, kind: str, name: str, needed: set, optional: set,
                  poll_s: float) -> dict | None:
    """The exposure core both callable-script gates share. `kind` is the action kind the
    observation carries AND the noun the teaching prose uses ("util" / "script").
    """
    ctx = loop.ctx
    present = _exposable(ctx, needed, optional)
    if not present:
        return None   # nothing exposable — a declared-but-unset secret fails visibly inside
    # A DECLINE withholds the CREDENTIAL, not the util: a declined required name is scrubbed
    # from the child env (executor) and the call proceeds; a util that cannot work without it
    # fails INSIDE, visibly. Forbidding a UTIL is its `util:` grant's job.
    undecided = [s for s in present if secret_state(ctx, s) == "undecided"]
    if not undecided:
        return None
    if ctx.depth > 0:
        return {"kind": kind, "name": name, "pending_secrets": undecided,
                "reason": "secret exposure to this routine is not yet granted, and a "
                          "sub-workflow cannot ask the user — the TOP-LEVEL run must call "
                          f"{kind} {name!r} once to trigger the approval."}
    ask = handle_ask(loop, {
        "question": f"Expose secret{'s' if len(undecided) > 1 else ''} "
                    f"{', '.join(undecided)} to routine '{ctx.routine.slug}'? Its {kind} "
                    f"call '{name}' declares them.",
        "mode": "blocking",
        "request": [f"secret:{s}" for s in undecided],
        # the prose must match the enforcement: a TIMEOUT refuses the call, a DECLINE only
        # withholds the credentials
        "default": f"the {kind} is NOT run and the secrets stay unexposed until decided "
                   f"(declining withholds just these credentials and lets the {kind} run "
                   "without them; it does not disable it)"},
        poll_s)
    if ask.get("dialog"):
        # an ask-back on the exposure request: the call does not run, the operator's words
        # reach the run here, and calling it again re-submits — and replaces — the request
        # (a request's subject is its entity ids, whoever files it)
        return {"kind": kind, "name": name, "pending_secrets": undecided, **still_pending(ask)}
    if not ask.get("answered"):
        return {"kind": kind, "name": name, "pending_secrets": undecided, **still_pending(ask),
                "reason": "the secret-exposure request is still open — do other work and "
                          f"retry the {kind} once it is settled."}
    # a decline given AT THE ASK is already in `ctx.denied_now` (requests.apply_decision), so
    # the executor withholds it exactly like a `grants:` false row and the call proceeds
    return None
