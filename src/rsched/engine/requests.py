"""Typed ACCESS REQUESTS — the run side of the four-state grant model (entities.py).

An `ask_user` carrying `request: "<class>:<name>"` asks the user for an entity grant;
the Decisions page answers it with one of four decisions (allow/deny × now/forever).
This module owns the run-side mechanics:

- `request_denial` validates the request INSIDE the schema-retry cycle (a malformed,
  redundant or already-declined request is corrected and never costs a turn) — the
  sibling of `interact.recreate_denial` in `completion.action_candidate`.
- `apply_decision` seeds the run's one-time overlay (RunContext.granted_now/denied_now —
  in-memory on purpose: a resumed leg starts empty and re-asks) and rebuilds the live
  policy + transport schema, so an allow-now takes effect on the very next turn.
- `apply_boot_decisions` replays decisions the user made while no run was live (the
  deferred-answer files consumed at run start, before the prompt is composed).

CONFIG is never written here: forever-decisions are persisted by the WEB layer when the
user clicks (web.grants_apply) — the engine only bridges them into the current run's
overlay, because its loaded routine config predates the click. No run and no engine code
writes routine.yaml.
"""

from __future__ import annotations

from .. import entities, utils_lib

DECISIONS = ("allow_now", "allow_forever", "deny_now", "deny_forever")

# What each decision means for the run — the observation's teaching line and the
# deferred-answer digest phrase share it (the web layer writes the same wording into the
# answer file's `text` so every surface reads one vocabulary).
DECISION_PHRASES = {
    "allow_now": "allowed for THIS RUN only — usable now; the grant does not survive "
                 "this run",
    "allow_forever": "allowed permanently — recorded in the routine's config (and usable "
                     "now)",
    "deny_now": "declined for this run — work without it and do not re-request it now",
    "deny_forever": "declined permanently — never request it again (the routine page can "
                    "revisit this)",
}


def request_ids(action: dict) -> list[str]:
    """The request field as a list of raw ids. The MODEL always sends one id (the schema
    says string); engine-synthesized asks (the secrets gate) may carry several — one
    decision then covers them all, like the old combined secret approval.
    """
    raw = action.get("request")
    if not raw:
        return []
    return [str(r) for r in raw] if isinstance(raw, list) else [str(raw)]


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
            problems.append(
                f'request: {raw!r} is not a grant-entity id — use "<class>:<name>" with '
                f'class one of {", ".join(entities.CLASSES)} (e.g. "util:discord", '
                f'"fs-write:~/project", "secret:FOO_KEY", "recreate:util-slug")')
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
    loop.action_schema = schema_for_kinds(effective_kinds(loop.allowed_tools, ctx.grants))


def apply_decision(loop, ids: list[str], decision: str, *, account: str = "") -> str:
    """Seed the run overlay with one user decision over `ids` and rebuild the live
    policy. Returns the phrase for observations/transcripts. Forever-decisions were
    already persisted web-side — here they only bridge into the current run.
    """
    ctx = loop.ctx
    allow = decision in ("allow_now", "allow_forever")
    for eid in ids:
        if allow:
            ctx.granted_now.add(eid)
            ctx.denied_now.discard(eid)
            if account and eid.startswith("connection:"):
                ctx.grant_args[eid] = account
        else:
            ctx.denied_now.add(eid)
            ctx.granted_now.discard(eid)
    rebuild_policy(loop)
    return DECISION_PHRASES[decision]


def observation_text(ids: list[str], decision: str) -> str:
    return f"{', '.join(ids)}: {DECISION_PHRASES[decision]}."


def apply_boot_decisions(loop, deferred_qa: list[dict]) -> None:
    """Decisions made on the Decisions page while no run was live, consumed at run
    start (inbox.collect_deferred_answers keeps `request`/`decision` on the pair). An
    'allow now' decided between runs grants exactly the run that consumes it — this one.
    Applied BEFORE the prompt is composed, so CAPABILITIES already shows the grant.
    """
    ctx = loop.ctx
    touched = False
    for pair in deferred_qa:
        ids, decision = pair.get("request") or [], pair.get("decision") or ""
        if not ids or decision not in DECISIONS:
            continue
        allow = decision in ("allow_now", "allow_forever")
        for eid in ids:
            (ctx.granted_now if allow else ctx.denied_now).add(eid)
            if allow and pair.get("account") and eid.startswith("connection:"):
                ctx.grant_args[eid] = str(pair["account"])
        touched = True
    if touched:
        rebuild_policy(loop)
