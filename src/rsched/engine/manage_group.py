"""The `manage_group` action handler — a CONVERSATION manages routine GROUPS from chat (D61).

The operator's rule (D61, option A): routine group management must be fully reachable via an
ACTION, not only the web surface (the routines page's group rows since D80 retired the
/groups subpage). One compact action kind carries every operation through a `verb` field,
mirroring the `/api/groups` surface the routines page uses:

    verb=list                                    → the whole store (default + every group)
    verb=create   name=… [members=…] [on_failure=…] [cron=…]
    verb=update   target=<group id> [name=…] [members=…] [on_failure=…] [cron=…]
                  [paused=…]
    verb=delete   target=<group id>
    verb=set-default  on_failure=<stop|continue>
    verb=run      target=<group id>              → arm a sequential fire (Phase B)

`cron` is the GROUP schedule (D71, R312): the chain fires on it, member crons are
suppressed while it is set. The server tz is recorded beside it, exactly as the web layer
writes it; an empty string clears the schedule. This is how a user's group-scheduling
request is completed by the conversation itself, with no operator round-trip to the web
(R312 — direct user requirement 2026-08-11). `paused` (update only) gates the cron without
touching it — nothing in a paused group auto-fires, an explicit run still works.

Members are flat ordered slugs (weak-model-friendly schema); the handler wraps them into
the store's member records. A flow with an inbound and an outbound end brackets the group
(D90, 2026-08-16): an inbound-router member placed first, an outbound-sender member placed
last — the F292 two-pass `split` flag is retired.

It reuses the SAME `rsched.groups` store + `rsched.group_runs` the endpoints call — one source
of truth, so a group created from chat and one created from the page are identical, and member
validation against the live registry (a group can never name a routine that does not exist)
matches `api_groups._validate_members`.

Structural rule (mirrors `create_routine` / `detach`): valid ONLY from a ROOT CONVERSATION
(depth 0). Groups are instance-level operator state that FIRE other routines; a scheduled
routine has no user in the loop to authorize reorganizing them, and a within-reply child must
not do it as a side effect. The engine ALSO only surfaces the kind to a root conversation
(loop.allowed_tools injection), so the handler gate is defence in depth, not the only gate.
"""

from __future__ import annotations

from .. import group_runs, groups, registry, schedule
from ..pending import READ_ONLY_VERBS
from .detach import _is_root_conversation
from .run_context import RunContext

VERBS = ("list", "create", "update", "delete", "set-default", "run")


def _known_slugs(ctx: RunContext) -> set[str]:
    """Every real routine slug, from the live registry — the set a group member must be in."""
    return set(registry.scan(ctx.server).keys())


def _reject(reason: str) -> dict:
    return {"kind": "manage_group", "rejected": True, "reason": reason}


def _slug_list_or_error(action: dict, field: str):
    """The action's `field` as a list of strings, or a teaching rejection. (None, None)
    when the field is absent — 'leave unchanged' for update, empty for create.
    """
    if field not in action:
        return None, None
    raw = action.get(field)
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        return None, _reject(f"manage_group: '{field}' must be a list of routine-slug strings")
    return list(raw), None


def _members_or_error(ctx: RunContext, action: dict):
    """Wrap the action's flat `members` (ordered slugs) into the store's member RECORDS,
    validated against the live registry. Returns (records, None) or (None, reject);
    (None, None) when the field is absent — 'leave unchanged' for update, [] for create.
    """
    slugs, err = _slug_list_or_error(action, "members")
    if err:
        return None, err
    if slugs is None:
        return None, None
    known = _known_slugs(ctx)
    unknown = [m for m in slugs if m not in known]
    if unknown:
        return None, _reject(
            f"manage_group: unknown routine(s) {sorted(unknown)} — a group may only name "
            "routines that exist in the registry")
    return [{"slug": s} for s in slugs], None


def _proposal_line(ctx: RunContext, action: dict, verb: str) -> str:
    """What this proposal would DO, in one line, with the target group named as the operator
    will see it. Resolved here rather than in the renderer because this is where the store is:
    a run told only "queued" cannot name the pending change in its own finish summary (R1183,
    R1200), and a `run` proposal that does not say how many members it would fire is
    indistinguishable from one that would fire none.
    """
    gid = str(action.get("target") or "").strip()
    group = groups.get(ctx.server.routines_home, gid) if gid else None
    if gid and group is None:
        named = f"the unknown group {gid!r} (no such group — the proposal will fail review)"
    elif group:
        members = len(group.get("members") or [])
        named = f"group {str(group.get('name') or gid)!r} ({gid}, {members} member(s) today)"
    else:
        named = f"a new group {str(action.get('name') or '').strip()!r}"
    if verb == "run":
        return f"proposed: fire {named} as one sequential chain"
    changes = []
    if "name" in action:
        changes.append(f"name → {str(action.get('name') or '').strip()!r}")
    if isinstance(action.get("members"), list):
        changes.append(f"members → {list(action['members'])}")
    if "on_failure" in action:
        changes.append(f"on_failure → {str(action.get('on_failure') or '').strip()!r}")
    if "cron" in action:
        cron = str(action.get("cron") or "").strip()
        changes.append(f"schedule → {cron!r}" if cron else "schedule → cleared")
    if "paused" in action:
        changes.append(f"paused → {bool(action.get('paused'))}")
    detail = f" — {'; '.join(changes)}" if changes else ""
    return f"proposed: {verb} {named}{detail}"


def _queued_obs(ctx: RunContext, action: dict, verb: str) -> dict:
    """A scheduled run's group proposal, filed for the operator (F328) — the twin of
    create_routine's. R353 needed BOTH: a routine plus the two-phase group it belongs in.
    """
    from ..pending import queue

    fields = {k: action[k] for k in ("verb", "target", "name", "members",
                                     "on_failure", "cron", "paused") if k in action}
    fields["verb"] = verb
    proposal = _proposal_line(ctx, action, verb)
    rec = queue(ctx.server.routines_home, kind="manage_group", routine=ctx.routine.slug,
                run_id=ctx.run_id, fields=fields, summary=proposal)
    return {"kind": "manage_group", "verb": verb, "queued": True, "id": rec["id"],
            "proposal": proposal,
            "next": ("Nothing changed yet, and nothing will until the user approves it — you "
                     "have no user in the loop, so this went to the Decisions page as a "
                     "proposal. Do NOT re-issue it: a second call queues a second proposal. "
                     "Your next run learns the outcome from a message in your inbox.")}


def handle_manage_group(ctx: RunContext, action: dict) -> dict:  # noqa: PLR0911 — a verb dispatcher: each verb's guard returns its own teaching rejection, one flat handler by design
    """Run one group operation from a root conversation. Returns the observation dict the loop
    records and renders. Bad input is a teaching rejection (corrected by the model), never a
    crash.

    Action fields (reused from the shared schema):
      verb        — one of VERBS (required)
      target      — the group id (required for update/delete/run)
      name        — the group's display name (required for create; optional for update)
      members     — ordered routine slugs (optional; create/update)
      on_failure  — 'stop' | 'continue' (optional for create/update; required for set-default)
      paused      — gate the group's cron without clearing it (optional; update)
    """
    verb = str(action.get("verb") or "").strip()
    if verb not in VERBS:
        return _reject(f"manage_group requires a 'verb' field, one of {list(VERBS)}; got "
                       f"{verb!r}")

    home = ctx.server.routines_home
    gid = str(action.get("target") or "").strip()

    # No user in the loop → a MUTATING verb becomes a proposal on the Decisions page (F328),
    # not a refusal. `list` still answers directly: it writes nothing, and a run that cannot
    # read the group store cannot propose a correct change to it.
    if ctx.depth > 0 and verb not in READ_ONLY_VERBS:
        return _reject(
            "manage_group cannot CHANGE anything from inside a child run — a sub-workflow must "
            "not reshape routine groups as a side effect. Report what you would change in your "
            "finish summary and let the run that started you decide.")
    if not _is_root_conversation(ctx) and verb not in READ_ONLY_VERBS:
        # `run` is NOT proposable. It arms an ephemeral group fire — it writes no config and the
        # materializer (web/api_pending._materialize_group) cannot build it, so a queued `run`
        # became a dead "create it" card the operator could only discard (screenshots 2026-09-03).
        # Fire is time-sensitive: an approval hours later fires a stale chain. So it fails LOUDLY
        # here (R1200's ask: fire, or fail with the reason), never a queue.
        if verb == "run":
            return _reject(
                "manage_group run cannot fire a group from a run with no user in the loop. "
                "Unlike a config change (create/update/delete/set-default), a fire is ephemeral "
                "and cannot be queued for later approval — an approval hours later would fire a "
                "stale chain. Ask the user to fire the group, or report in your finish summary "
                "which group needs firing and why.")
        return _queued_obs(ctx, action, verb)

    if verb == "list":
        return {"kind": "manage_group", "verb": "list",
                "default_on_failure": groups.default_on_failure(home),
                "groups": groups.list_groups(home)}

    if verb == "set-default":
        try:
            value = groups.set_default_on_failure(home, str(action.get("on_failure") or "").strip())
        except ValueError as exc:
            return _reject(f"manage_group set-default: {exc}")
        return {"kind": "manage_group", "verb": "set-default", "default_on_failure": value}

    if verb == "create":
        members, err = _members_or_error(ctx, action)
        if err:
            return err
        cron = str(action.get("cron") or "").strip()
        try:
            rec = groups.create(home, name=str(action.get("name") or "").strip(),
                                members=members or [],
                                on_failure=_normalize_on_failure(action),
                                cron=cron, tz=schedule.server_tz() if cron else "")
        except ValueError as exc:
            return _reject(f"manage_group create: {exc}")
        return {"kind": "manage_group", "verb": "create", "group": rec}

    # update / delete / run all need an existing group id
    if not gid:
        return _reject(f"manage_group {verb} requires 'target' (the group id, e.g. 'grp-1a2b3c4d')")

    if verb == "delete":
        if not groups.delete(home, gid):
            return _reject(f"manage_group delete: no group {gid!r}")
        return {"kind": "manage_group", "verb": "delete", "deleted": gid}

    if verb == "update":
        current = groups.get(home, gid)
        if current is None:
            return _reject(f"manage_group update: no group {gid!r}")
        members, err = _members_or_error(ctx, action)
        if err:
            return err
        on_failure = _normalize_on_failure(action) if "on_failure" in action else groups._UNSET
        name = action.get("name")
        # key-presence semantics like members/on_failure: absent = unchanged, "" = clear
        new_cron = str(action.get("cron") or "").strip() if "cron" in action else None
        tz = None if new_cron is None else (schedule.server_tz() if new_cron else "")
        paused = bool(action.get("paused")) if "paused" in action else None
        try:
            updated = groups.update(home, gid,
                                    name=(str(name).strip() if name is not None else None),
                                    members=members, on_failure=on_failure,
                                    cron=new_cron, tz=tz, paused=paused)
        except ValueError as exc:
            return _reject(f"manage_group update: {exc}")
        if updated is None:
            return _reject(f"manage_group update: no group {gid!r}")
        return {"kind": "manage_group", "verb": "update", "group": updated}

    # verb == "run"
    group = groups.get(home, gid)
    if group is None:
        return _reject(f"manage_group run: no group {gid!r}")
    if not group.get("members"):
        return _reject(f"manage_group run: group {gid!r} has no members to fire")
    armed = group_runs.arm(home, group, default_on_failure=groups.default_on_failure(home),
                           armed_by="conversation")
    if armed is None:
        return _reject(f"manage_group run: group {gid!r} is already running (a group fires as "
                       "one chain at a time)")
    return {"kind": "manage_group", "verb": "run", "group_id": gid,
            "members": group.get("members", [])}


def _normalize_on_failure(action: dict) -> str | None:
    """The optional on_failure field: a stripped string, or None (inherit) when absent/blank."""
    val = str(action.get("on_failure") or "").strip()
    return val or None
