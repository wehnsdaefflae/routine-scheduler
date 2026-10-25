"""The SECRET-EXPOSURE gate — the approval a util or script needs before it sees a
credential.

Split out of `interact.py` (F393): the ask/answer protocol, this gate and library authoring
were three responsibilities in one file. This one owns a single question — may THIS call see
THESE secrets — answered from the routine's four-state grant standing (`grants:` rows are the
forever states, the run overlay the once states) and, when undecided, by asking the user
through the same decision record every other ask uses.

An OPTIONAL secret a util declares with `?` is never worth blocking a run for: it is withheld
silently and the run is told what it lost, so a degraded call beats a stalled one.

A DECLINED REQUIRED secret is withheld the same way and the call proceeds (F756): a decline
protects the credential, not the util — forbidding a util is a `util:` grant's job. That holds
for every call THIS GATE sees, because this gate only sees calls the RUN chose: `util`, a
`/util` slash command, and the routine's own `scripts/<name>.py`. The engine's own util calls
never reach here (the run chose neither the util nor its arguments), and the one that needs a
credential — `mediaops.vision_describe`, the vision fallback behind `view_image` — REFUSES on a
declined secret instead. That asymmetry is deliberate and is the operator's recorded answer to
D213 (2026-10-25): the engine refuses for calls IT chose to make and withholds for calls the RUN
chose. Read from the run's side it is one rule — a decline never silently degrades work the run
did not ask for: a call the run wrote comes back degraded and judgeable, a call the engine made
on its behalf comes back refused with the reason, rather than failing inside a util the run never
invoked. Before F756 both sites refused, and one declined name disabled every call of a util,
including calls needing none of it.

`secret_state` answers about ROWS and nothing else — never about an adjacent `util:` grant that
happens to declare the name. That is deliberate (F562): a granted util used to leave its own
declared secrets undecided, so the first call filed a SECOND blocking request for the same
capability, and the fix widened the REQUEST rather than this interpretation
(`engine/grantsecrets.py`, called where the decision record is written). Inferring exposure here
would be fewer lines and would make a util grant silently carry credentials.
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

    For a call the ENGINE makes on the run's behalf — the view_image vision fallback — which
    no exposure gate precedes. Such a call asks nothing (the run did not choose the util or
    its arguments, and an ask from inside a view would block it on a fallback it never asked
    for), but a decline is the user's answer already given, and handing the util the secret
    anyway overruled it.
    """
    needs = utils_run.util_needs(ctx.server.libraries_home, name)
    return [s for s in _exposable(ctx, needs.secrets, needs.optional)
            if secret_state(ctx, s) == "denied"]


def withheld_optional(ctx, optional: set[str]) -> list[str]:
    """The OPTIONAL (`?`-declared, F290) secrets present in the store that this run may NOT
    see — not granted to the routine. They never block a call or file an ask: the executor
    withholds them from the child env and the observation says so, so a public call runs
    prompt-free and an auth-needing one learns to request exposure explicitly.
    """
    from ..secrets import load_secrets
    own = _own_secrets(ctx)
    return sorted(s for s in (optional & set(load_secrets())) - own
                  if secret_state(ctx, s) != "granted")


class Withheld(NamedTuple):
    """What a call that RUNS does not get of the secrets it declares, by the two reasons a
    declared credential may be absent while the call still proceeds:

    * `optional` — F290/D51: a `?`-declared name the routine may not see. Never blocks, never
      asks; undecided ones are requestable, so they may be named.
    * `declined` — F756: a REQUIRED name the user DECLINED. A decline protects the credential,
      not the util, so it is withheld and the call runs without it. A count only in anything
      the model reads: a denial enumerates nothing (R17).
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
    """Both withholdings for one call's transitive declarations. The declined half is the
    same `secret_state` the gate reads, so a decline recorded in `grants:` and one given at
    the exposure ask (`requests._seed` writes `ctx.denied_now` before the gate resumes) mean
    the same thing here — one word, one meaning.
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
    """R2622: what a VERB-SCOPED declaration withheld from THIS call, as the observation
    field — `{"verb": <the verb that ran, or None>, "secrets": [names]}`, empty when the
    call tree binds no secret to a verb.

    It is reported rather than merely decided because the two are indistinguishable from
    inside the util: a verb-bound secret the engine withheld arrives exactly as a secret
    nobody provisioned, and the next move on that reading is provisioning a credential that
    is already provisioned (R2621). A call that names NO verb — the util's default path, or
    `--selftest` — withholds every bound name, so this is also how a default-path call learns
    that a credential exists for a verb it did not invoke.
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
    """D39: per-routine secret exposure, decided at CALL time through the FOUR-STATE grant
    model. A util call whose transitive `secrets:` declarations name secrets PRESENT in
    the store runs only once this routine may see them: `grants:` rows (`secret:<NAME>`
    true/false) are the forever states, the run overlay the once states, and an undecided
    name files ONE blocking access request covering every undecided secret (the D38 hold
    semantics apply; the web persists a forever-decision, an allow-now covers this run).
    Returns None to let the call proceed, or the refusing/pending observation.

    OPTIONAL secrets (`?`-declared, D51/F290) never reach this gate's ask or refusal: an
    optional secret the routine may not see is silently WITHHELD from the child env
    instead (withheld_optional_secrets), so a call that does not need it — a public
    page fetch — runs without prompting anyone.
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
    # F756: a DECLINE withholds the CREDENTIAL; it does not refuse the util. Until this, one
    # declined name refused EVERY call of the util — including a call needing none of it
    # (measured live: the GMAIL_* secrets granted and the FAU_* ones declined on `gu mail`,
    # after which even `--account default` was refused, so the grant UI offered a per-secret
    # decline the enforcement could not keep). A declined required name is now scrubbed from
    # the child env exactly as a `?`-declared optional one is (`withheld_from_call`, applied
    # in executor) and the call proceeds; a util that truly cannot work without it fails
    # INSIDE, visibly, with its own message — already the accepted outcome for a
    # declared-but-unset secret (`_exposable`). To forbid a UTIL, withhold its `util:` grant
    # or reserve it: a secret row is not that lever, and reading it as one overruled the user
    # in the one direction he cannot see.
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
        # F756: the prose must match the enforcement, or the question offers a decline the
        # engine cannot keep. A TIMEOUT still refuses the call (nobody decided); DECLINING
        # withholds just those credentials and lets the call run without them — it does not
        # disable the util, which is a `util:` grant's job.
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
    # F756: a decline given AT THE ASK is the same user intent as a `grants:` false row, so it
    # takes the same path — `requests.apply_decision` has already put the entity in
    # `ctx.denied_now`, which makes `secret_state` read "denied" for the executor's
    # `withheld_from_call`. Two different meanings for one word is the defect, not the fix:
    # whichever way he says no, the credential is withheld and the call proceeds without it.
    return None
