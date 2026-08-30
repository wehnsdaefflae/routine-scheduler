"""The SECRET-EXPOSURE gate — the approval a util or script needs before it sees a
credential.

Split out of `interact.py` (F393): the ask/answer protocol, this gate and library authoring
were three responsibilities in one file. This one owns a single question — may THIS call see
THESE secrets — answered from the routine's four-state grant standing (`grants:` rows are the
forever states, the run overlay the once states) and, when undecided, by asking the user
through the same decision record every other ask uses.

An OPTIONAL secret a util declares with `?` is never worth blocking a run for: it is withheld
silently and the run is told what it lost, so a degraded call beats a stalled one.
"""

from __future__ import annotations

from .. import utils_lib, utils_run
from . import requests
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
    if name in ("list", "show") or not utils_lib.exists(home, name):
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
    ctx = loop.ctx
    name = str(action.get("name") or "")
    if not scripts.exists(ctx.routine.dir, name):
        return None                     # the missing-script path exposes no secrets
    needed, _net, optional = scripts.needs(ctx.routine.dir, name,
                                           ctx.server.libraries_home)
    return _gate_secrets(loop, kind="script", name=name, needed=needed,
                         optional=optional, poll_s=poll_s)


def _gate_secrets(loop, *, kind: str, name: str, needed: set, optional: set,
                  poll_s: float) -> dict | None:
    """The exposure core both callable-script gates share. `kind` is the action kind the
    observation carries AND the noun the teaching prose uses ("util" / "script").
    """
    ctx = loop.ctx
    from ..secrets import load_secrets
    required = needed - optional
    # D103: a name the routine holds in its OWN store needs no exposure decision — it is
    # the routine's, and its value shadows the central one for this run.
    present = sorted((required & set(load_secrets())) - _own_secrets(ctx)) if required else []
    if not present:
        return None   # nothing exposable — a declared-but-unset secret fails visibly inside

    def _state(secret: str) -> str:
        return secret_state(ctx, secret)

    denied = [s for s in present if _state(s) == "denied"]
    if denied:
        # R17: a DENIAL enumerates nothing — the refusal must not hand back the very
        # names it refused (the transcript event keeps them for the user's surfaces;
        # the model-facing reason and rendering carry a count only).
        n = len(denied)
        return {"kind": kind, "name": name, "declined_secrets": denied,
                "reason": f"the user has declined exposing {n} secret"
                          f"{'s' if n != 1 else ''} this {kind} call declares to this "
                          f"routine — the {kind} was not run. The mapping is editable on the "
                          f"routine page (secret exposure); work without this {kind}, or file "
                          "a deferred ask_user explaining why it is needed."}
    undecided = [s for s in present if _state(s) == "undecided"]
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
        "default": f"the {kind} is NOT run and the secrets stay unexposed until allowed"},
        poll_s)
    if not ask.get("answered"):
        return {"kind": kind, "name": name, "pending_secrets": undecided,
                "pending_approval": True, "qid": ask.get("qid"),
                "reason": "the secret-exposure request is still open — do other work and "
                          f"retry the {kind} once it is settled."}
    decision = str(ask.get("decision") or "")
    if decision.startswith("allow"):
        return None
    # R17: the decline reason stays GENERIC — a count and the decision's shared phrase,
    # never the names (requests.observation_text would enumerate the entity ids, which
    # is right for entities the model itself requested, and wrong for a refusal).
    n = len(undecided)
    phrase = requests.DECISION_PHRASES.get(decision, "declined")
    return {"kind": kind, "name": name, "declined_secrets": undecided,
            "decision": ask.get("decision"),
            "reason": f"the user declined exposing {n} secret{'s' if n != 1 else ''} "
                      f"this {kind} call declares to this routine — the {kind} was not run "
                      f"({phrase})."}
