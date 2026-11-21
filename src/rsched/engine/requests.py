"""Typed ACCESS REQUESTS — the run side of the grant model (entities.py).

An `ask_user` with `request: "<class>:<name>"` is answered on the Decisions page with
allow/deny × now/forever, plus `allow once` for entities.ONCE_CLASSES (D65/D76). Validation
happens before the request becomes a turn (`availability.request_denial`); this module is
what happens once the user has DECIDED:

- `apply_decision` seeds the run's overlay (RunContext.granted_now/denied_now — in-memory on
  purpose: a resumed leg re-asks) and rebuilds policy + schema for the very next turn.
- `apply_deferred_decisions` replays decisions on DEFERRED asks, at boot and at the live turn
  boundary, into the same overlay — so a web-side grant reaches the running run's enforcers.

CONFIG is never written here: forever-decisions are persisted by the web layer at the click
(web.grants_apply); the engine only bridges them into the current run.
"""

from __future__ import annotations

from .. import entities, utils_run

DECISIONS = ("allow_now", "allow_once", "allow_forever", "deny_now", "deny_forever")

# What each decision means for the run — one vocabulary for the observation, the deferred
# digest and the web's answer `text`.
DECISION_PHRASES = {
    "allow_now": "allowed for THIS RUN only — usable now; the grant does not survive "
                 "this run",
    "allow_once": "allowed for ONE action only — your next matching action spends it, "
                  "then the engine revokes it; request again if you need another use. "
                  "For an fs root, ANY util call counts as the matching action (the "
                  "sandbox mounts granted roots wholesale), so do the file work first",
    "allow_forever": "allowed permanently — recorded in the routine's config (and usable "
                     "now). A util grant enables the NAMED util only: its permission "
                     "doc activates for conduct, but sibling utils and tag classes stay "
                     "off, each requestable separately",
    "deny_now": "declined for this run — work without it and do not re-request it now",
    "deny_forever": "declined permanently — never request it again (the routine page can "
                    "revisit this)",
}


def rebuild_policy(loop) -> None:
    """Fold the run's decisions over the CONFIG-derived base policy and re-project the
    schema. Always base+overlay, never stacked.
    """
    from . import remind
    from .kindsurface import effective_kinds, schema_for_kinds

    ctx = loop.ctx
    loop.grants = ctx.grants = loop.base_grants.with_overlay(ctx.granted_now,
                                                             ctx.denied_now)
    remind.refresh(loop)   # a reminders:* grant raises the level the store was read at
    if loop._finish_reserved:
        # the reserved finish turn keeps its finish-only grammar (the policy still updates)
        return
    loop.action_schema = schema_for_kinds(
        effective_kinds(loop.allowed_tools, ctx.grants),
        reminders=bool(ctx.grants and ctx.grants.reminders_on))


def _seed(ctx, eid: str, decision: str, *, account: str = "") -> None:
    """One decision, one entity, into the run overlay (both seams' kernel). `allow_once` on a
    non-once-grantable class is skipped — fail CLOSED, never widened to allow_now.
    """
    if decision in ("allow_now", "allow_once", "allow_forever"):
        if decision == "allow_once":
            cls = eid.partition(":")[0]
            if cls not in entities.ONCE_CLASSES:
                return
            ctx.granted_once.add(eid)
        ctx.granted_now.add(eid)
        ctx.denied_now.discard(eid)
        if account and eid.startswith("connection:"):
            ctx.grant_args[eid] = account
    else:
        ctx.denied_now.add(eid)
        ctx.granted_now.discard(eid)
        ctx.granted_once.discard(eid)


def apply_decision(loop, ids: list[str], decision: str, *, account: str = "") -> str:
    """Seed the run overlay with one decision over `ids`, rebuild the policy, return the
    phrase.
    """
    for eid in ids:
        _seed(loop.ctx, eid, decision, account=account)
    rebuild_policy(loop)
    return DECISION_PHRASES[decision]


def observation_text(ids: list[str], decision: str) -> str:
    return f"{', '.join(ids)}: {DECISION_PHRASES[decision]}."


# Observation keys meaning the call did NOT execute the granted thing (gate refusal or
# bounced back); a once-grant survives them — spent by USE, not by attempt.
_UNDISPATCHED_KEYS = ("declined", "declined_secrets", "pending_secrets",
                      "unknown_target", "self_target", "target_unreachable",
                      "bad_fire_at", "error", "missing")


def _once_match(eid: str, action: dict, ctx) -> bool:
    """Does this dispatched action USE the once-granted entity? One clause per ONCE_CLASSES
    member; secret:/fs-*: match the action that RECEIVES the entity (D76's coarser promise).
    """
    from ..grantpolicy import is_runs_path
    cls, _, name = eid.partition(":")
    kind = action.get("kind")
    if cls == "action":
        return kind == name
    if cls == "util":
        from ..grants import split_util_verb

        bare, verb = split_util_verb(name)
        args = action.get("args") or []
        return (kind == "util" and str(action.get("name") or "") == bare
                and (not verb or bool(args and args[0] == verb)))
    if cls == "secret":     # spent by the util call the var is actually injected into:
        if kind != "util":  # only utils DECLARING it (calls: tree included) receive it
            return False
        needed = utils_run.util_needs(ctx.server.libraries_home,
                                      str(action.get("name") or "")).secrets
        return name in needed
    if cls in ("fs-read", "fs-write"):
        return _fs_once_match(name, action, ctx)
    if cls == "runs":                      # spent by reading ANOTHER run's tree
        if kind not in ("read_file", "view_image"):
            return False
        own = f"runs/{ctx.run_ts}/"
        paths = [str(action.get("path") or ""),
                 *(str(p) for p in action.get("paths") or [])]
        return any(p and is_runs_path(p) and not p.removeprefix("./").startswith(own)
                   for p in paths)
    return False


def _fs_once_match(root_name: str, action: dict, ctx) -> bool:
    """An fs once-grant is RECEIVED by ANY util invocation (the sandbox mounts granted roots
    wholesale) and by a file action under the root (write implies read).
    """
    kind = action.get("kind")
    if kind == "util":
        return True
    if kind not in ("read_file", "view_image", "write_file", "edit_file"):
        return False
    from pathlib import Path

    from ..paths import expand, within
    root = Path(root_name)
    for raw in [action.get("path"), *(action.get("paths") or [])]:
        if not raw:
            continue
        target = expand(str(raw))
        if not target.is_absolute():
            target = ctx.routine.dir / target
        if within(root, target):
            return True
    return False


def consume_once_grants(loop, action: dict, obs: dict) -> set[str]:
    """Spend `allow once` grants (D65): the FIRST dispatched matching action revokes the grant
    at this boundary and rebuilds the policy. A gate refusal (_UNDISPATCHED_KEYS) leaves it
    armed. Returns the spent ids for `spent_notice`.
    """
    ctx = loop.ctx
    if not ctx.granted_once or any(obs.get(k) for k in _UNDISPATCHED_KEYS):
        return set()
    spent = {eid for eid in ctx.granted_once if _once_match(eid, action, ctx)}
    if spent:
        ctx.granted_once -= spent
        ctx.granted_now -= spent
        rebuild_policy(loop)
    return spent


def spent_notice(spent: set[str], action: dict) -> str:
    """The engine line appended to the consuming action's observation, so the revocation is
    learned where it happens. It NAMES the consumer: an unrelated util call can spend an fs
    once-grant (D93).
    """
    kind = str(action.get("kind") or "")
    what = f"util {action.get('name')!r} call" if kind == "util" else f"{kind} action"
    note = ""
    if kind == "util" and any(e.startswith(("fs-read:", "fs-write:")) for e in spent):
        note = (" An fs once-grant is received by ANY util invocation — to spend it on "
                "file work, do the file actions BEFORE unrelated util calls.")
    return (f"\n[ONCE-GRANT SPENT: {', '.join(sorted(spent))} — consumed by this {what}; "
            f"the grant is now revoked. Request it again if you need another use.{note}]")


def apply_deferred_decisions(loop, deferred_qa: list[dict]) -> None:
    """Access-request decisions on DEFERRED asks, consumed at run BOOT (before the prompt is
    composed) and at the live TURN BOUNDARY (control.drain_injections), so "usable now" holds
    for the next action. An 'allow now' grants exactly the run that consumes it.
    """
    ctx = loop.ctx
    touched = False
    for pair in deferred_qa:
        ids, decision = pair.get("request") or [], pair.get("decision") or ""
        if not ids or decision not in DECISIONS:
            continue
        for eid in ids:
            _seed(ctx, eid, decision, account=str(pair.get("account") or ""))
        touched = True
    if touched:
        rebuild_policy(loop)
