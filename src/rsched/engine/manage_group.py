"""The `manage_group` action handler — a CONVERSATION manages routine GROUPS from chat (D61).

The operator's rule (D61, option A): routine group management must be fully reachable via an
ACTION, not only the `/groups` web subpage (which STAYS — this is additive). One compact action
kind carries every operation through a `verb` field, mirroring the `/api/groups` surface the
Groups page already uses:

    verb=list                                    → the whole store (default + every group)
    verb=create   name=… [members=…] [on_failure=…] [cron=…]
    verb=update   target=<group id> [name=…] [members=…] [on_failure=…] [cron=…]
    verb=delete   target=<group id>
    verb=set-default  on_failure=<stop|continue>
    verb=run      target=<group id>              → arm a sequential fire (Phase B)

`cron` is the GROUP schedule (D71, R312): member 0 fires on it, the rest chain on
completion, member crons are suppressed while it is set. The server tz is recorded beside
it, exactly as the Groups page writes it; an empty string clears the schedule. This is how
a user's group-scheduling request is completed by the conversation itself, with no
operator round-trip to /groups (R312 — direct user requirement 2026-08-11).

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
from .detach import _is_root_conversation
from .run_context import RunContext

VERBS = ("list", "create", "update", "delete", "set-default", "run")


def _known_slugs(ctx: RunContext) -> set[str]:
    """Every real routine slug, from the live registry — the set a group member must be in."""
    return set(registry.scan(ctx.server).keys())


def _reject(reason: str) -> dict:
    return {"kind": "manage_group", "rejected": True, "reason": reason}


def _members_or_error(ctx: RunContext, action: dict):
    """Validate `members` against the live registry. Returns (members, None) or (None, reject).
    A missing `members` field yields (None, None) — 'leave unchanged' for update, [] for create.
    """
    if "members" not in action:
        return None, None
    raw = action.get("members")
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        return None, _reject("manage_group: 'members' must be a list of routine-slug strings")
    known = _known_slugs(ctx)
    unknown = [m for m in raw if m not in known]
    if unknown:
        return None, _reject(
            f"manage_group: unknown routine(s) {sorted(unknown)} — a group may only name "
            "routines that exist in the registry")
    return list(raw), None


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
    """
    if not _is_root_conversation(ctx):
        return _reject(
            "manage_group is only available from a top-level conversation — a scheduled routine "
            "or a within-reply child cannot manage routine groups. Group management is initiated "
            "by a conversation, with the user.")

    verb = str(action.get("verb") or "").strip()
    if verb not in VERBS:
        return _reject(f"manage_group requires a 'verb' field, one of {list(VERBS)}; got "
                       f"{verb!r}")

    home = ctx.server.routines_home
    gid = str(action.get("target") or "").strip()

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
        members, err = _members_or_error(ctx, action)
        if err:
            return err
        on_failure = _normalize_on_failure(action) if "on_failure" in action else groups._UNSET
        name = action.get("name")
        # key-presence semantics like members/on_failure: absent = unchanged, "" = clear
        new_cron = str(action.get("cron") or "").strip() if "cron" in action else None
        tz = None if new_cron is None else (schedule.server_tz() if new_cron else "")
        try:
            updated = groups.update(home, gid,
                                    name=(str(name).strip() if name is not None else None),
                                    members=members, on_failure=on_failure,
                                    cron=new_cron, tz=tz)
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
