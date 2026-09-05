"""Can this run ask for THAT? — the availability check behind an access request.

Split out of `requests.py` (F393): deciding whether a request is even coherent is separable from
applying the decision that comes back.

The distinction this draws is the useful one: a request for something that does not exist, or
that the routine already holds, or that the user has permanently declined, should never reach
the Decisions page at all. Each of those gets its own teaching refusal instead, so the model
learns why rather than waiting on a question nobody can answer.
"""

from __future__ import annotations

from .. import entities, utils_lib


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
            # A real action kind that is simply not gateable (create_routine, manage_lane,
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
    if cls == "reminders":
        current = g.reminders if g is not None else "none"
        order = ("none", "local", "global")
        if order.index(current) >= order.index(name):
            return [f"the reminder layer is already at {current!r}, which covers {eid} — "
                    "use the `remind` field directly"]
        return []
    # recreate: — only meaningful for a slug that existed and was deleted by the user
    home = ctx.server.libraries_home
    if utils_lib.exists(home, name):
        return [f"util {name!r} exists — nothing to recreate; revise it with write_util"]
    if not utils_lib.was_deleted(home, name):
        return [f"util {name!r} never existed — no unlock needed; create it with "
                "write_util directly"]
    return []
