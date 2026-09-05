"""The `manage_lane` action handler — a CONVERSATION manages routine LANES from chat (D61).

The operator's rule (D61, option A): lane management must be fully reachable via an ACTION,
not only the web surface (the routines page's lane rows). One compact action kind carries
every operation through a `verb` field, mirroring the `/api/lanes` surface the routines page
uses:

    verb=list                                    → the whole store (default + every lane)
    verb=create   name=… [members=…] [on_failure=…] [cron=…]
    verb=update   target=<lane id> [name=…] [members=…] [on_failure=…] [cron=…]
                  [paused=…]
    verb=delete   target=<lane id>
    verb=set-default  on_failure=<stop|continue>
    verb=run      target=<lane id>               → arm a sequential fire (Phase B)

**This kind covers the TEMPORAL axis and nothing else.** A lane decides when its members fire
and in what order (`rsched.lanes`). A DOMAIN — the shared config block, the shared store, the
notes boundary — is an ordinary per-routine setting living in that routine's own routine.yaml
(`domain:`), which no run writes. So there is deliberately no `manage_domain` verb and no
domain field here: a conversation that wants to move a routine between domains proposes it as
a config patch (`ask_user` with `config_patch`), which the user approves and the WEB writes.
The split is the point: reordering when routines fire must never be able to change what a
routine can reach (docs/lanes-domains.md).

`cron` is the LANE schedule (D71, R312): the chain fires on it, member crons are
suppressed while it is set. The server tz is recorded beside it, exactly as the web layer
writes it; an empty string clears the schedule. This is how a user's scheduling request is
completed by the conversation itself, with no operator round-trip to the web. `paused`
(update only) gates the cron without touching it — nothing in a paused lane auto-fires, an
explicit run still works.

Members are flat ordered slugs (weak-model-friendly schema); the handler wraps them into
the store's member records. A chain fires each member ONCE, so a flow with an inbound and an
outbound end BRACKETS the lane (D90): an inbound-router member placed first, an outbound-sender
member placed last — two single-purpose members, never one member run twice.

It reuses the SAME `rsched.lanes` store + `rsched.lane_runs` the endpoints call — one source
of truth, so a lane created from chat and one created from the page are identical. Member
validation against the live registry (a lane can never name a routine that does not exist)
matches `api_lanes._validate_members`.

Structural rule (mirrors `create_routine`): the kind is conversation-INITIATED, not
conversation-only (F328). Every depth-0 run is offered it — `loopsetup` injects it for all of
them — because a scheduled run holding a finished design had no way to hand it over except back
to the operator by hand (R353). What varies is what a verb DOES. From a root conversation it
applies; from any other depth-0 run it writes a PROPOSAL to the Decisions page, which the user
materializes through this same store with one click. `list` answers directly either way: it
writes nothing; a run that cannot read the lane store cannot propose a correct change to it.

A within-reply CHILD (depth > 0) is refused outright and never sees the kind. The queue is for a
run that HAS a user, just not right now; a child run is a side effect of one turn and reorganizing
what fires other routines is not something it may do on the way past.
"""

from __future__ import annotations

from .. import lane_runs, lanes, registry, schedule
from ..pending import READ_ONLY_VERBS
from .detach import _is_root_conversation
from .run_context import RunContext

VERBS = ("list", "create", "update", "delete", "set-default", "run")


def _known_slugs(ctx: RunContext) -> set[str]:
    """Every real routine slug, from the live registry — the set a lane member must be in."""
    return set(registry.scan(ctx.server).keys())


def _reject(reason: str) -> dict:
    return {"kind": "manage_lane", "rejected": True, "reason": reason}


def _slug_list_or_error(action: dict, field: str):
    """The action's `field` as a list of strings, or a teaching rejection. (None, None)
    when the field is absent — 'leave unchanged' for update, empty for create.
    """
    if field not in action:
        return None, None
    raw = action.get(field)
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        return None, _reject(f"manage_lane: '{field}' must be a list of routine-slug strings")
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
            f"manage_lane: unknown routine(s) {sorted(unknown)} — a lane may only name "
            "routines that exist in the registry")
    return [{"slug": s} for s in slugs], None


def _proposal_line(ctx: RunContext, action: dict, verb: str) -> str:
    """What this proposal would DO, in one line, with the target lane named as the operator
    will see it. Resolved here rather than in the renderer because this is where the store is:
    a run told only "queued" cannot name the pending change in its own finish summary (R1183,
    R1200). A `run` proposal that does not say how many members it would fire is
    indistinguishable from one that would fire none.
    """
    lane_id = str(action.get("target") or "").strip()
    lane = lanes.get(ctx.server.routines_home, lane_id) if lane_id else None
    if lane_id and lane is None:
        named = f"the unknown lane {lane_id!r} (no such lane — the proposal will fail review)"
    elif lane:
        members = len(lane.get("members") or [])
        named = f"lane {str(lane.get('name') or lane_id)!r} ({lane_id}, {members} member(s) today)"
    else:
        named = f"a new lane {str(action.get('name') or '').strip()!r}"
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
    """A scheduled run's lane proposal, filed for the operator (F328) — the twin of
    create_routine's. R353 needed BOTH: a routine plus the lane it belongs in.
    """
    from ..pending import queue

    fields = {k: action[k] for k in ("verb", "target", "name", "members",
                                     "on_failure", "cron", "paused") if k in action}
    fields["verb"] = verb
    proposal = _proposal_line(ctx, action, verb)
    rec = queue(ctx.server.routines_home, kind="manage_lane", routine=ctx.routine.slug,
                run_id=ctx.run_id, fields=fields, summary=proposal)
    return {"kind": "manage_lane", "verb": verb, "queued": True, "id": rec["id"],
            "proposal": proposal,
            "next": ("Nothing changed yet and nothing will until the user approves it — you "
                     "have no user in the loop, so this went to the Decisions page as a "
                     "proposal. Do NOT re-issue it: a second call queues a second proposal. "
                     "Your next run learns the outcome from a message in your inbox.")}


def handle_manage_lane(ctx: RunContext, action: dict) -> dict:  # noqa: PLR0911 — a verb dispatcher: each verb's guard returns its own teaching rejection, one flat handler by design
    """Run one lane operation. From a root conversation it applies; from any other depth-0 run
    a mutating verb becomes a proposal. Returns the observation dict the loop records and
    renders. Bad input is a teaching rejection (corrected by the model), never a crash.

    Action fields (reused from the shared schema):
      verb        — one of VERBS (required)
      target      — the lane id (required for update/delete/run)
      name        — the lane's display name (required for create; optional for update)
      members     — ordered routine slugs (optional; create/update)
      on_failure  — 'stop' | 'continue' (optional for create/update; required for set-default)
      cron        — the lane's schedule in server tz (optional; create/update; '' clears it)
      paused      — gate the lane's cron without clearing it (optional; update)
    """
    verb = str(action.get("verb") or "").strip()
    if verb not in VERBS:
        return _reject(f"manage_lane requires a 'verb' field, one of {list(VERBS)}; got "
                       f"{verb!r}")

    home = ctx.server.routines_home
    lane_id = str(action.get("target") or "").strip()

    # No user in the loop → a MUTATING verb becomes a proposal on the Decisions page (F328),
    # not a refusal. `list` still answers directly: it writes nothing; a run that cannot
    # read the lane store cannot propose a correct change to it.
    if ctx.depth > 0 and verb not in READ_ONLY_VERBS:
        return _reject(
            "manage_lane cannot CHANGE anything from inside a child run — a sub-workflow must "
            "not reshape the fire order of other routines as a side effect. Report what you "
            "would change in your finish summary and let the run that started you decide.")
    if not _is_root_conversation(ctx) and verb not in READ_ONLY_VERBS:
        # `run` is NOT proposable. It arms an ephemeral lane fire — it writes no config and the
        # materializer (web/api_pending._materialize_lane) cannot build it, so a queued `run`
        # is a dead "create it" card the operator can only discard. Fire is time-sensitive too:
        # an approval hours later fires a stale chain. So it fails LOUDLY here (R1200's ask:
        # fire, or fail with the reason), never a queue.
        if verb == "run":
            return _reject(
                "manage_lane run cannot fire a lane from a run with no user in the loop. "
                "Unlike a config change (create/update/delete/set-default), a fire is ephemeral "
                "and cannot be queued for later approval — an approval hours later would fire a "
                "stale chain. Ask the user to fire the lane, or report in your finish summary "
                "which lane needs firing and why.")
        return _queued_obs(ctx, action, verb)

    if verb == "list":
        return {"kind": "manage_lane", "verb": "list",
                "default_on_failure": lanes.default_on_failure(home),
                "lanes": lanes.list_lanes(home)}

    if verb == "set-default":
        try:
            value = lanes.set_default_on_failure(home, str(action.get("on_failure") or "").strip())
        except ValueError as exc:
            return _reject(f"manage_lane set-default: {exc}")
        return {"kind": "manage_lane", "verb": "set-default", "default_on_failure": value}

    if verb == "create":
        members, err = _members_or_error(ctx, action)
        if err:
            return err
        cron = str(action.get("cron") or "").strip()
        try:
            rec = lanes.create(home, name=str(action.get("name") or "").strip(),
                               members=members or [],
                               on_failure=_normalize_on_failure(action),
                               cron=cron, tz=schedule.server_tz() if cron else "")
        except ValueError as exc:
            return _reject(f"manage_lane create: {exc}")
        return {"kind": "manage_lane", "verb": "create", "lane": rec}

    # update / delete / run all need an existing lane id
    if not lane_id:
        return _reject(f"manage_lane {verb} requires 'target' — the lane id, which `verb=list` "
                       "returns (an opaque handle; do not construct one)")

    if verb == "delete":
        if not lanes.delete(home, lane_id):
            return _reject(f"manage_lane delete: no lane {lane_id!r}")
        return {"kind": "manage_lane", "verb": "delete", "deleted": lane_id}

    if verb == "update":
        current = lanes.get(home, lane_id)
        if current is None:
            return _reject(f"manage_lane update: no lane {lane_id!r}")
        members, err = _members_or_error(ctx, action)
        if err:
            return err
        on_failure = _normalize_on_failure(action) if "on_failure" in action else lanes._UNSET
        name = action.get("name")
        # key-presence semantics like members/on_failure: absent = unchanged, "" = clear
        new_cron = str(action.get("cron") or "").strip() if "cron" in action else None
        tz = None if new_cron is None else (schedule.server_tz() if new_cron else "")
        paused = bool(action.get("paused")) if "paused" in action else None
        try:
            updated = lanes.update(home, lane_id,
                                   name=(str(name).strip() if name is not None else None),
                                   members=members, on_failure=on_failure,
                                   cron=new_cron, tz=tz, paused=paused)
        except ValueError as exc:
            return _reject(f"manage_lane update: {exc}")
        if updated is None:
            return _reject(f"manage_lane update: no lane {lane_id!r}")
        return {"kind": "manage_lane", "verb": "update", "lane": updated}

    # verb == "run"
    lane = lanes.get(home, lane_id)
    if lane is None:
        return _reject(f"manage_lane run: no lane {lane_id!r}")
    if not lane.get("members"):
        return _reject(f"manage_lane run: lane {lane_id!r} has no members to fire")
    armed = lane_runs.arm(home, lane, default_on_failure=lanes.default_on_failure(home),
                          armed_by="conversation")
    if armed is None:
        return _reject(f"manage_lane run: lane {lane_id!r} is already running (a lane fires as "
                       "one chain at a time)")
    return {"kind": "manage_lane", "verb": "run", "lane_id": lane_id,
            "members": lane.get("members", [])}


def _normalize_on_failure(action: dict) -> str | None:
    """The optional on_failure field: a stripped string, or None (inherit) when absent/blank."""
    val = str(action.get("on_failure") or "").strip()
    return val or None
