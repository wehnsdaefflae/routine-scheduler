"""Typed ACCESS REQUESTS — the run side of the grant model (entities.py).

An `ask_user` carrying `request: "<class>:<name>"` asks the user for an entity grant;
the Decisions page answers it with one of four decisions (allow/deny × now/forever) —
five for once-grantable classes (entities.ONCE_CLASSES), which also offer `allow once`
(D65; D76 extends it to secret:/fs-*: with the coarser util-invocation-level spend).
This module owns the run-side mechanics:

- `request_denial` validates the request INSIDE the schema-retry cycle (a malformed,
  redundant or already-declined request is corrected and never costs a turn) — the
  sibling of `interact.recreate_denial` in `completion.action_candidate`.
- `apply_decision` seeds the run's one-time overlay (RunContext.granted_now/denied_now —
  in-memory on purpose: a resumed leg starts empty and re-asks) and rebuilds the live
  policy + transport schema, so an allow-now takes effect on the very next turn.
- `apply_deferred_decisions` replays decisions made on the Decisions page for DEFERRED
  asks — at run start (the deferred-answer files consumed before the prompt is composed)
  AND at the live turn boundary (a deferred ask answered while the run is running, R118):
  both seams end in the same overlay, so a web-side grant reaches the running run's
  enforcers (validate_action, the util sandbox's roots, declared-only env injection)
  instead of waiting for the next run.

CONFIG is never written here: forever-decisions are persisted by the WEB layer when the
user clicks (web.grants_apply) — the engine only bridges them into the current run's
overlay, because its loaded routine config predates the click. No run and no engine code
writes routine.yaml.
"""

from __future__ import annotations

from .. import entities, utils_lib, utils_run

DECISIONS = ("allow_now", "allow_once", "allow_forever", "deny_now", "deny_forever")

# What each decision means for the run — the observation's teaching line and the
# deferred-answer digest phrase share it (the web layer writes the same wording into the
# answer file's `text` so every surface reads one vocabulary).
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


def request_ids(action: dict) -> list[str]:
    """The request field as a list of ids. The MODEL always sends one id (the schema
    says string); engine-synthesized asks (the secrets gate) may carry several — one
    decision then covers them all, like the old combined secret approval.

    Well-shaped ids come back CANONICAL (entities.parse_entity — fs paths expanded to
    one absolute form), so the pending record, the web's config write and the run
    overlay all speak the same id: a model asking `fs-write:~/x` must yield the same
    root everywhere, or the granted path never matches the enforcers' comparisons.
    A malformed id passes through raw for request_denial to teach.
    """
    raw = action.get("request")
    if not raw:
        return []
    ids = [str(r) for r in raw] if isinstance(raw, list) else [str(raw)]
    return [f"{p[0]}:{p[1]}" if (p := entities.parse_entity(rid)) else rid for rid in ids]


def request_denial(loop, action: dict) -> list[str]:
    """Validate an access request inside the schema-retry cycle. Empty = requestable;
    otherwise each problem names the way out (never a bare rejection). Mirrors
    recreate_denial's shape so completion.action_candidate chains them.
    """
    if action.get("kind") != "ask_user" or not action.get("request"):
        return []
    ctx = loop.ctx
    if ctx.depth > 0:
        return ["sub-workflows cannot request access — name the need in your finish "
                "summary so the top-level run can request it"]
    problems: list[str] = []
    for raw in request_ids(action):
        parsed = entities.parse_entity(raw)
        if parsed is None:
            # A real action kind that is simply not gateable (create_routine, manage_group,
            # finish, ...) would otherwise get the generic "not a grant-entity id" copy —
            # which lists "action" as a valid class and so reads as self-contradictory
            # (routine-improver:20260814-015412 retried against it). Name the actual rule.
            acls, _, aname = raw.partition(":")
            aname = aname.strip()
            if acls == "action" and aname:
                from ..grants import GATED_KINDS
                from .actionschema import KINDS
                if aname in KINDS and aname not in GATED_KINDS:
                    problems.append(
                        f"request: the {aname!r} action kind exists but is not grantable "
                        f"per-routine — the requestable action kinds are "
                        f'{", ".join(GATED_KINDS)}. {aname!r} is wired to the run kind '
                        "(e.g. conversation-only), so no grant can switch it on here — "
                        "raise the need in a report or a plain ask_user instead")
                    continue
            problems.append(
                f'request: {raw!r} is not a grant-entity id — use "<class>:<name>" with '
                f'class one of {", ".join(entities.CLASSES)} (e.g. "util:discord", '
                f'"fs-write:~/project", "secret:FOO_KEY", "machine:gpu-box", '
                f'"recreate:util-slug")')
            continue
        cls, name = parsed
        eid = f"{cls}:{name}"
        g = loop.grants
        state = g.entity_state(eid) if g is not None else "undecided"
        if state in ("denied_forever", "denied_now"):
            problems.append(g.request_route(eid))
            continue
        if state == "granted_now":
            problems.append(f"{eid} is already granted for this run — use it directly "
                            "instead of re-requesting")
            continue
        problems.extend(_availability(loop, cls, name, eid))
    return problems


def _availability(loop, cls: str, name: str, eid: str) -> list[str]:  # noqa: C901, PLR0911, PLR0912 — per-class exits, each its own teaching text
    """Is this undecided entity actually requestable here? [] = yes; else the correction.
    Existence checks run against the LIVE vocabularies (library requires, provider
    registry, machine catalog, secrets store) — never against a snapshot.
    """
    ctx = loop.ctx
    g = loop.grants
    cfg = ctx.routine
    if cls == "action":
        if g is None or g.allows_kind(name):
            return [f"{eid} is already enabled for this routine — use the {name} action "
                    "directly"]
        return []
    if cls == "util":
        if g is not None and name in g.utils:
            return [f"{eid} is already enabled — call the util directly"]
        if g is None or name not in g.gated_utils:
            if utils_lib.exists(ctx.server.libraries_home, name):
                return [f"util {name!r} is not reserved — every routine may call it; no "
                        "request needed"]
            return [f"no util {name!r} exists — there is nothing to unlock; create it "
                    "with write_util (or request action:write_util if that is off)"]
        return []
    if cls == "secret":
        if (cfg.grants or {}).get(eid) is True:
            return [f"{eid} is already exposed to this routine — call the util directly"]
        from ..secrets import load_secrets
        if name not in load_secrets():
            return [f"secret {name} is not provisioned in the central store — a grant "
                    "cannot conjure it; ask the user in prose (deferred ask_user) to add "
                    "it under Settings → Secrets first"]
        return []
    if cls == "connection":
        from ..oauth.providers import PROVIDERS
        if name not in PROVIDERS:
            return [f"unknown connection provider {name!r} — known providers: "
                    f"{', '.join(sorted(PROVIDERS))}"]
        if (cfg.connections or {}).get(name):
            return [f"{eid} is already bound to this routine — its token is injected "
                    "into any util that declares it"]
        from ..oauth import store as oauth_store
        accounts = [c["account"] for c in oauth_store.list_connections()
                    if c.get("provider") == name]
        if not accounts:
            return [f"no {name} account is connected on this instance — ask the user in "
                    "prose (deferred ask_user) to connect one under Settings → "
                    "Connections first"]
        if len(accounts) > 1:
            return [f"several {name} accounts are connected ({', '.join(sorted(accounts))}) "
                    "— the account choice is the user's: ask in prose (deferred ask_user) "
                    "naming the purpose, so they bind the right one on the routine page"]
        return []
    if cls == "machine":
        if name not in ctx.server.machines:
            known = ", ".join(sorted(ctx.server.machines)) or "(none defined)"
            return [f"no machine {name!r} in the instance catalog — known machines: "
                    f"{known}; ask the user in prose to add it under Settings → Machines"]
        if name in (cfg.machines or []):
            return [f"{eid} is already bound to this routine — act on it with the "
                    "`remote` util"]
        return []
    if cls in ("fs-read", "fs-write"):
        from pathlib import Path

        from ..paths import within
        if entities.never_grantable_fs(name):
            return [f"{eid} covers an instance credential store — never grantable, to "
                    "any routine, by design"]
        target = Path(name)
        if within(cfg.dir, target) or target == cfg.dir:
            return [f"{eid} lies inside your own working directory — already fully "
                    "readable and writable"]
        roots = cfg.fs_read_roots if cls == "fs-read" else cfg.fs_write_roots
        if any(within(root, target) or target == root for root in roots or []):
            return [f"{eid} is already covered by this routine's "
                    f"{'read' if cls == 'fs-read' else 'write'} roots — use it directly"]
        return []
    if cls == "runs":
        current = g.run_history if g is not None else "none"
        order = ("none", "last", "all")
        if order.index(current) >= order.index(name):
            return [f"previous-run access at depth {current!r} already covers "
                    f"{eid} — read runs/ directly"]
        return []
    if cls == "workflows":
        if g is not None and g.workflows == "generate":
            return [f"{eid} is already enabled — set a subtask's workflow to 'generate'"]
        return []
    # recreate: — only meaningful for a slug that existed and was deleted by the user
    home = ctx.server.libraries_home
    if utils_lib.exists(home, name):
        return [f"util {name!r} exists — nothing to recreate; revise it with write_util"]
    if not utils_lib.was_deleted(home, name):
        return [f"util {name!r} never existed — no unlock needed; create it with "
                "write_util directly"]
    return []


def rebuild_policy(loop) -> None:
    """Fold the run's one-time decisions over the CONFIG-derived base policy and
    re-project the transport schema — a granted kind becomes generatable on the next
    turn, a denied one stays unrepresentable. Always base+overlay, never stacked.
    """
    from .kindsurface import effective_kinds, schema_for_kinds

    ctx = loop.ctx
    loop.grants = ctx.grants = loop.base_grants.with_overlay(ctx.granted_now,
                                                             ctx.denied_now)
    if getattr(loop, "_finish_reserved", False):
        # The reserved finish turn's finish-only grammar must survive a decision that
        # lands at the same boundary (the drain bridge) — the policy update above still
        # matters (resource consumers read it), but the last turn stays a finish.
        return
    loop.action_schema = schema_for_kinds(effective_kinds(loop.allowed_tools, ctx.grants))


def _seed(ctx, eid: str, decision: str, *, account: str = "") -> None:
    """One decision, one entity, into the run overlay — the shared kernel of the live
    (apply_decision) and deferred (apply_deferred_decisions) seams. `allow_once` arms the
    consume tracker beside the grant; the web layer refuses it for non-once-grantable
    classes, and this seam mirrors that fail-CLOSED (skip, never a silent widening to
    allow_now: an unconsumable once-grant would revoke nothing all run).
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
    """Seed the run overlay with one user decision over `ids` and rebuild the live
    policy. Returns the phrase for observations/transcripts. Forever-decisions were
    already persisted web-side — here they only bridge into the current run.
    """
    for eid in ids:
        _seed(loop.ctx, eid, decision, account=account)
    rebuild_policy(loop)
    return DECISION_PHRASES[decision]


def observation_text(ids: list[str], decision: str) -> str:
    return f"{', '.join(ids)}: {DECISION_PHRASES[decision]}."


# Observation keys that mean the dispatched call did NOT execute the granted thing — a
# user gate refused it pre-execution (declined write_util / refused secrets) or the
# handler bounced it back for correction (unknown target, a failed file read, a missing
# util). A once-grant survives those: it is spent by USE, not by attempt.
_UNDISPATCHED_KEYS = ("declined", "declined_secrets", "pending_secrets",
                      "unknown_target", "self_target", "bad_fire_at", "error", "missing")


def _once_match(eid: str, action: dict, ctx) -> bool:  # noqa: PLR0911 — one exit per grant class
    """Does this successfully-dispatched action USE the once-granted entity? One clause
    per entities.ONCE_CLASSES member. Turn-action classes match exactly (their use IS
    the turn); secret:/fs-*: match the action that RECEIVES the entity — the coarser
    D76 promise entities.py documents.
    """
    from ..grantpolicy import is_runs_path
    cls, _, name = eid.partition(":")
    kind = action.get("kind")
    if cls == "action":
        return kind == name
    if cls == "util":
        return kind == "util" and str(action.get("name") or "") == name
    if cls == "secret":     # spent by the util call the var is actually injected into:
        if kind != "util":  # only utils DECLARING it (calls: tree included) receive it
            return False
        needed, _net, _opt = utils_run.util_needs(ctx.server.libraries_home,
                                                  str(action.get("name") or ""))
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
    if cls == "workflows":
        return kind == "subtask" and str(action.get("workflow") or "") == name
    return False


def _fs_once_match(root_name: str, action: dict, ctx) -> bool:
    """An fs once-grant is RECEIVED by (a) ANY util invocation — the sandbox mounts the
    run's granted roots wholesale and the engine cannot see which paths the subprocess
    touched (the coarseness D76's option text states) — and (b) a file action on a path
    under the root (a read under a write root is a real use too: write implies read,
    F294).
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
    """Spend `allow once (this action only)` grants (D65): the FIRST successfully-
    dispatched matching action revokes the grant at the same boundary — dropped from the
    overlay and the policy rebuilt, so the very next turn's schema and validators no
    longer carry it. A schema retry or a validation rejection never reaches here (it
    never becomes a turn), and a user gate refusing the call pre-execution
    (_UNDISPATCHED_KEYS) leaves the grant armed. Returns the spent ids so the loop can
    tell the model (spent_notice).
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
    """The engine line appended to the consuming action's observation — the model must
    learn the revocation at the boundary it happens, or its next matching attempt reads
    as an unexplained denial (docs/prompt-anatomy.md pins it). D93 (F350): the notice
    NAMES the consuming action — an fs once-grant is received by ANY util invocation
    (the sandbox mounts granted roots wholesale), so an unrelated first util call can be
    the spender, and without attribution that burn read as silent loss (2026-08-16: the
    run's opening codemap call spent the fs-write grant meant for a later scrub).
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
    """Access-request decisions answered on the Decisions page for DEFERRED asks
    (inbox.collect_deferred_answers keeps `request`/`decision` on the pair). Two seams
    consume them: run BOOT (decided while no run was live — applied before the prompt
    is composed, so CAPABILITIES already shows the grant) and the live TURN BOUNDARY
    (decided while the run was running — control.drain_injections bridges it, so the
    "usable now" phrase the answer carries is true for the very next action: the next
    util call's sandbox and the file actions read ctx.granted_now live, R118). An
    'allow now' grants exactly the run that consumes it — this one.
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
