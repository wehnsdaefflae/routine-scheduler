"""The consequence-reminder LAYER: pre-execution interception, and the turn-free authoring ops.

A reminder is a caution the model left itself about an action's unintended effect
(`rsched/reminders.py` owns the store). This file is the runtime half:

- **interception** — before an action executes, its canonical string (`actionschema.canon`) is
  tested against the live set; on a match the action is HELD and the caution shown, because a
  caution that arrives with the observation arrives after the consequence.
- **the ops** — `remind` and `remind_feedback` ride ANY action as no-turn side fields, like
  `note`, so the realisation is recorded on the turn it happens.

One hold per REMINDER per guard scope (engine/guardscope.py; rebuilt on resume by
`remind_ledger`) — re-emitting the held action is the confirmation, and a gate that never
yields livelocks — and one hold per action however many reminders match.

A reminder is one configuration of a hook (D152); each write is mirrored into the hook store
(`engine/hookmirror`).
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import remind_dead
from .. import reminders as store
from ..reminders import LABEL_HELP, Reminder
from . import assist_label, hookmirror, remind_points, remind_write, remindfields
from . import hold as hold_seam

#: This layer's name in the shared hold ledger, which this layer keys by REMINDER ID (one
#: hold per caution per run) — the rule layer's own budget is never spent by a reminder.
SOURCE = "reminder"


def configure(loop) -> None:
    """This layer's run state. The live set is read ONCE (see `load`) and then kept in
    step with this run's own ops.
    """
    loop.reminders = load(loop)
    loop.reminders_level = level_of(loop.ctx.grants)   # what `refresh` compares against
    loop.reminder_replayed = set()   # payloads a re-driven finish already applied
    loop.reminder_asked_back = False  # this turn's op came back as an ask-back (finishgate)
    loop.reminder_pending = []       # fires still owed a label — what the nudge names
    loop.reminder_owed = {}          # id → holds this run not yet labelled; one label per hold
    loop.reminder_nudge = 0
    # `turn:answer` watermark, so the moment is the ARRIVAL EDGE rather than "a reply exists"
    loop.reminder_user_replies = int(getattr(loop.ctx, "user_replies", 0) or 0)


def replay_key(field: str, payload: object) -> str:
    """One side field's payload as the replay ledger stores it (a re-driven finish dedups on it)."""
    return json.dumps([field, payload], sort_keys=True)


def level_of(grants) -> str:
    """The capability level a set would be read at — "none" for an ungated direct construction."""
    return grants.reminders if grants is not None else "none"


def load(loop) -> list[Reminder]:
    """The live set for this run, read ONCE at construction.

    Once, not per turn: the composed prompt is an append-only caching contract. The in-memory
    list keeps step with every op this run applies.
    """
    ctx = loop.ctx
    try:
        return store.active(ctx.routine.dir, ctx.server.reminders_home, level_of(ctx.grants),
                            list(getattr(ctx.routine, "shared_reminders", None) or []),
                            ctx.run_id, routines_home=ctx.server.routines_home)
    except OSError:
        return []


def refresh(loop) -> None:
    """Re-read the set if the capability LEVEL has moved since it was read.

    At level `none` the store is not read, so a grant landing mid-run would leave the set empty
    — and a definition write rebuilt from it would drop every earlier row. A re-read can only
    ADD: what this run applied is on disk by the time any op returns.
    """
    level = level_of(loop.ctx.grants)
    if level != loop.reminders_level:
        loop.reminders = load(loop)
        loop.reminders_level = level


def hold(loop, action: dict, rendered: str) -> dict | None:  # noqa: ARG001 — the seam calls every source with ONE signature; this layer matches on the rendering alone
    """This layer's answer for the shared pre-execution seam (`engine/hold.py`): the
    observation the model reads instead of the action's result, or None to let it execute.

    `rendered` is the canonical string, computed once by the seam. Only a reminder that has not
    held yet in this guard scope holds; each that does records a `fires` and owes one label.
    """
    if not loop.reminders:
        return None
    hits = [h for h in remind_points.matching_at(loop, "action", rendered)
            if not hold_seam.held_before(loop, SOURCE, h.id)]
    if not hits:
        return None
    for hit in hits:
        hold_seam.mark_held(loop, SOURCE, hit.id)
    _count_fires(loop, hits)
    # the tally rides the observation (obs_hold renders `store.looks_too_broad` from it)
    live = {h.id: h for h in loop.reminders}
    return {"kind": "reminder_hold", "action": rendered,
            "reminders": [{"id": h.id, "scope": h.scope, "description": h.description,
                           "owner": h.owner,
                           "stats": dict(live.get(h.id, h).stats)}
                          for h in hits]}


def _count_fires(loop, hits: list[Reminder]) -> None:
    """Record a fire on every reminder that just matched, and arm the label it is owed.

    Shared by every fire point so `fires` is one denominator for the labels. The tally is
    disk-owned (`store.record`) and mirrored into the in-memory set.
    """
    for hit in hits:
        _replace(loop, hit, stats=store.record(loop.ctx.routine.dir, hit, "fires"))
        loop.reminder_owed[hit.id] = loop.reminder_owed.get(hit.id, 0) + 1
    # turns the model has to volunteer the label before the engine asks once
    # (a `did`/`didnt` is only known a turn AFTER the action ran)
    loop.reminder_pending = [h.id for h in hits]
    loop.reminder_nudge = 2


def _fired_line(hit: Reminder, target_name: str) -> str:
    """One line, one shape, for every non-holding kind — what fired, on what, and the caution.

    A FOREIGN hook names its owner so the target can tell it may dispose of it.
    """
    whose = f"set by `{hit.owner}`" if hit.foreign else "your own caution"
    tail = (f" ({hit.owner} set this: carry remind_feedback with disposition keep/mute/remove to "
            f"decide its fate, and {LABEL_HELP})" if hit.foreign
            else f" (label it with remind_feedback: {LABEL_HELP})")
    return f"[REMINDER {hit.id} — {whose}, on this turn's {target_name}] {hit.description}{tail}"


def at_observation(loop, action: dict, obs: dict) -> str:
    """The tail appended to an observation for the kinds that CANNOT hold — "" when nothing
    fired. Costs no turn: these kinds describe what already happened, so nothing can be held.

    Targets (plain strings, one regex engine): `result` — the observation as the model reads
    it; `prose` — the turn's `say`; `turn:<moment>` — the moment's name plus the observation
    text, so a pattern may anchor on `^turn:finish`.
    """
    if not loop.reminders:
        return ""
    from .observations import format_observation

    result = format_observation(obs)
    prose = str(action.get("say") or "")
    fired: list[Reminder] = []
    targets = [("result", result, "result"), ("prose", prose, "prose")]
    for moment, text in _turn_moments(loop, action, obs):
        targets.append((f"turn:{moment}", f"turn:{moment}\n{text}", f"{moment} turn"))
    for kind, target, _name in targets:
        for hit in remind_points.matching_at(loop, kind, target):
            if hit not in fired:
                fired.append(hit)      # one line per reminder, however many kinds it matched
    if not fired:
        return ""
    _count_fires(loop, fired)
    names = {k: n for k, _t, n in targets}
    return "\n" + "\n".join(_fired_line(h, names.get(h.kind, h.kind)) for h in fired)


def _turn_moments(loop, action: dict, obs: dict) -> list[tuple[str, str]]:
    """Which special moments THIS turn is, as (moment, text) pairs — usually none.

    `first` is the opening turn of the run; `finish` is checked by `at_finish` instead (the
    finish never reaches an observation); `question` is an `ask_user`; `answer` is a user reply
    that arrived with this turn; `error` is an observation reporting a failure.
    """
    from .observations import format_observation, is_failure

    text = format_observation(obs)
    out: list[tuple[str, str]] = []
    if int(getattr(loop.ctx, "turn", 0) or 0) <= 1:
        out.append(("first", text))
    if action.get("kind") == "ask_user":
        out.append(("question", text))
    if int(getattr(loop, "reminder_user_replies", 0) or 0) < int(
            getattr(loop.ctx, "user_replies", 0) or 0):
        loop.reminder_user_replies = int(getattr(loop.ctx, "user_replies", 0) or 0)
        out.append(("answer", text))
    if is_failure(obs):
        out.append(("error", text))
    return out


def at_finish(loop, action: dict) -> str:
    """The `turn:finish` moment — the tail the finish observation carries, "" when nothing
    fired. The finish produces no ordinary observation, so this is its own fire point; it never
    holds, because the finish gate owns what defers a finish.
    """
    if not loop.reminders:
        return ""
    target = f"turn:finish\n{action.get('summary') or ''!s}"
    fired = remind_points.matching_at(loop, "turn:finish", target)
    if not fired:
        return ""
    _count_fires(loop, fired)
    return "\n" + "\n".join(_fired_line(h, "finish turn") for h in fired)


def apply_ops(loop, action: dict, poll_s: float, *, replayable: bool = False) -> str:
    """Apply this action's `remind` / `remind_feedback` fields and return the ENGINE NOTE the
    observation carries — "" when the action had neither.

    Applied AFTER the interception check, so a reminder cannot hold the action it rode in on.

    `replayable` marks a call site the engine re-drives with the same fields (the finish gate
    hands the same finish back): each field's payload is then applied at most once per guard
    scope, or a deferred finish would label one hold several times. Keyed per FIELD because an
    asked-back op was not applied (`_approve_global` withdraws its key) and must apply on re-send.
    """
    loop.reminder_asked_back = False     # set by `_approve_global`, read by the finish gate
    fields = [f for f in ("remind_feedback", "remind") if action.get(f)]
    if replayable and fields:
        fields = [f for f in fields if replay_key(f, action[f]) not in loop.reminder_replayed]
        if not fields:
            return ""
        loop.reminder_replayed.update(replay_key(f, action[f]) for f in fields)
    labels = action["remind_feedback"] if "remind_feedback" in fields else []
    notes = [assist_label.apply(loop, e, _apply_feedback)          # one entry or a LIST (F925)
             for e in remindfields.feedback_entries(labels) if e]
    if "remind" in fields:
        notes.append(_apply_op(loop, action["remind"], poll_s))
    notes.append(_label_nudge(loop, action))
    lines = [n for n in notes if n]
    return ("\n" + "\n".join(f"[REMINDERS: {n}]" for n in lines)) if lines else ""


def _label_nudge(loop, action: dict) -> str:
    """Ask ONCE for a label a fire is still owed.

    Never a hard requirement (rejecting an action for a bookkeeping field would block the work):
    asked once, two turns after the fire. What stays unlabelled shows as `fires` minus labels.
    """
    if not loop.reminder_pending:
        return ""
    if isinstance(action.get("remind_feedback"), dict):
        rid = str(action["remind_feedback"].get("id") or "")
        loop.reminder_pending = [r for r in loop.reminder_pending if r != rid]
        return ""
    loop.reminder_nudge -= 1
    if loop.reminder_nudge > 0:
        return ""
    owed = ", ".join(loop.reminder_pending)
    loop.reminder_pending = []
    return (f"{owed} fired and is STILL unlabelled — carry remind_feedback now that you know "
            f"how it turned out ({LABEL_HELP}). An unlabelled fire is a turn spent for no "
            "evidence, and the pattern cannot be tuned without it")


def _apply_feedback(loop, fb: dict) -> str:
    """Record one label — for a hold of this reminder, in this run, not labelled yet. A label
    with no hold behind it is evidence about nothing.
    """
    rid = str(fb.get("id") or "")
    label = str(fb.get("label") or "")
    target = store.find(loop.reminders, rid)
    if target is None:
        return (f"no reminder {rid!r} is live for this run, so the {label!r} label was not "
                "recorded — the ids are in the hold you are answering")
    if loop.reminder_owed.get(rid, 0) < 1:
        return (f"no hold of {rid} in this run is waiting for a label, so the {label!r} label "
                "was not recorded — a label answers one hold, once")
    loop.reminder_owed[rid] -= 1
    tally = store.record(loop.ctx.routine.dir, target, label)
    _replace(loop, target, stats=tally)
    counts = " / ".join(f"{n} {f}" for f in store.LABELS if (n := tally.get(f)))
    note = f"{rid} labelled {label} ({tally.get('fires', 0)} fires: {counts or 'none labelled'})"
    return note + _apply_disposition(loop, target, fb.get("disposition"))


def _apply_disposition(loop, target: Reminder, disposition: object) -> str:
    """The TARGET's say over a hook ANOTHER routine set on it, applied at the fire.

    `remove` also records the refusal so the owner cannot set the pattern again. A disposition
    on the routine's OWN reminder is refused — that is a `remind` op's job.
    """
    if disposition is None:
        return ""
    if not target.foreign:
        return (f"; no disposition applied — {target.id} is your own reminder, not one another "
                "routine set on you: revise or delete it with a `remind` op")
    applied = remind_write.dispose(loop, target, str(disposition))
    if not applied:
        return f"; no disposition applied — {target.id} is no longer in your store"
    if applied == "remove":
        _remove_from_set(loop, target)
        return (f"; REMOVED (set by {target.owner}) — it fires no more and {target.owner} cannot "
                "set that pattern on you again")
    _replace(loop, target, disposition=applied)
    if applied == "mute":
        loop.reminders = [r for r in loop.reminders if r.id != target.id]
        return f"; MUTED for the rest of this run (set by {target.owner}, who still sees it)"
    return f"; kept (set by {target.owner})"


def _remove_from_set(loop, target: Reminder) -> None:
    """Drop a disposed-of hook from this run's live set (the store write is `store.dispose`'s)."""
    loop.reminders = [r for r in loop.reminders if r.id != target.id]


def _resolve_target(loop, op: dict, rid: str) -> Reminder | str:
    """The reminder a `revise` or `delete` names — the live set first, the SHARED STORE second.

    A `reach: listed` global row is in no non-listing routine's live set, yet a curator must be
    able to name it: reach decides when a row FIRES, not who may curate it. The fallback runs
    only when the op NAMES `scope: global`; every authority check still runs on the result.

    Returns the reminder, or the refusal note; the two misses stay textually distinct.
    """
    verb = str(op.get("op") or "")
    if (target := store.find(loop.reminders, rid)) is not None:
        return target
    if str(op.get("scope") or "") == "global":
        home = loop.ctx.server.reminders_home
        try:
            curated = store.load_global(home)
        except OSError:
            curated = []
        if (target := store.find(curated, rid)) is not None:
            return target
        return (f"no reminder {rid!r} is in the shared store — nothing was {verb}d; the "
                "curated ids are the Library tab's reminder rows (a global row this routine "
                "does not list never appears in a hold)")
    return (f"no reminder {rid!r} is live for this run — nothing was {verb}d; the live ids are "
            "in the holds you have seen. If it is a CURATED row this routine does not list, "
            "name `scope: global` and it is resolved against the shared store")


def _apply_op(loop, op: dict, poll_s: float) -> str:
    verb = str(op.get("op") or "")
    scope = str(op.get("scope") or "local")
    if verb == "add":
        return _add(loop, op, scope, poll_s)
    rid = str(op.get("id") or "")
    if (foreign := str(op.get("target") or "")) or verb == "dead":
        return remind_write.curate(loop, op, foreign, rid, verb)
    if isinstance(target := _resolve_target(loop, op, rid), str):
        return target
    if scope != target.scope and op.get("scope") is not None:
        return (f"{rid} is a {target.scope} reminder and a {verb} cannot move it — to promote a "
                "proven local reminder, `add` it with scope global (its evidence is per-routine "
                "and starts fresh there), then delete the local one")
    # Ask the dial again about the TARGET's scope: the write gate asked about the op's (default
    # local), so a `local` routine could otherwise rewrite a curated reminder.
    if (grants := loop.ctx.grants) is not None and (
            denial := grants.reminder_denial(target.scope)):
        return f"{rid} is a {target.scope} reminder, so nothing was {verb}d — {denial}"
    if target.scope == "global" and (gate := _approve_global(loop, verb, target, op, poll_s)):
        return gate
    if verb == "delete":
        _remove(loop, target)
        if target.scope == "global":
            _commit(loop, f"delete reminder {rid}", target)
        return f"{rid} deleted ({target.scope})"
    revised = Reminder(id=target.id, scope=target.scope, created_run=target.created_run,
                       stats=target.stats,
                       regex=str(op.get("regex") or target.regex),
                       description=str(op.get("description") or target.description),
                       reach=str(op.get("reach") or target.reach),
                       kind=store.read_kind(op.get("kind")) if op.get("kind") else target.kind)
    _replace(loop, target, regex=revised.regex, description=revised.description,
             reach=revised.reach, kind=revised.kind)
    _persist(loop, revised, f"revise reminder {rid}")
    return (f"{rid} revised ({target.scope}, {revised.kind}) — now /{revised.regex}/ "
            f"{revised.description}")


def _set_for_other(loop, op: dict, target: str) -> str:
    """Set a hook in ANOTHER routine's store — the note the run reads back.

    It lands in the target's local store and the TARGET disposes of it on a fire; it never
    joins this run's live set. A refusal (the target's cap, a held or removed pattern) comes
    back as the note, not an exception — a refused hook is not a failed turn.
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        return ("sub-workflows cannot set a hook on another routine — it binds a run nobody in "
                "this tree supervises; name it in your summary instead")
    target_dir = Path(ctx.server.routines_home) / target
    if not target_dir.is_dir():
        return f"no routine '{target}' is installed — nothing was set"
    try:
        hook = store.set_for(target_dir, regex=str(op["regex"]),
                             description=str(op["description"]),
                             kind=store.read_kind(op.get("kind")), owner=ctx.routine.slug,
                             run_ts=ctx.run_ts)
    except store.ReminderWriteError as exc:
        return str(exc)
    hookmirror.reminder_written(loop, hook, target=target)
    return (f"set {hook.id} on {target} ({hook.kind}) — /{hook.regex}/ fires in ITS runs from "
            f"its next one on, and {target} decides on each fire whether to keep, mute or "
            "remove it; you cannot revise or delete it")


def _add(loop, op: dict, scope: str, poll_s: float) -> str:
    if target := str(op.get("target") or ""):
        return _set_for_other(loop, op, target)
    ctx = loop.ctx
    local = [r for r in loop.reminders if r.scope == "local"]
    if scope == "local" and len(local) >= store.MAX_LOCAL:
        return (f"this routine already holds {store.MAX_LOCAL} local reminders, the cap — "
                "delete one that its tally shows is not earning its turns before adding another")
    # The store this reminder joins as (id, pattern) pairs — for global, EVERY library record,
    # so a new id cannot collide with a `listed` row this routine does not hold.
    joined = ([(rec["id"], rec["regex"]) for rec in store.records(ctx.server.reminders_home)]
              if scope == "global" else [(r.id, r.regex) for r in local])
    kind = store.read_kind(op.get("kind"))
    # Duplicates are per (pattern, KIND) within the SAME store: a local shadowing a global is
    # designed precedence, and promotion needs that overlap for one turn.
    if any(r.regex == op.get("regex") and r.kind == kind and r.scope == scope
           for r in loop.reminders):
        return (f"a {scope} {kind} reminder with that exact pattern is already live — revise it "
                "instead of adding a second one that would fire on the same moments")
    taken = {r.id for r in loop.reminders} | {i for i, _ in joined}
    rid = store.new_id(ctx.run_ts, remind_dead.reserved_ids(ctx.routine.dir, taken))
    reminder = Reminder(id=rid, regex=str(op["regex"]), description=str(op["description"]),
                        scope=scope, created_run=ctx.run_id, stats=store.blank_stats(),
                        reach=str(op.get("reach") or "") if scope == "global" else "",
                        kind=kind)
    if scope == "global" and (gate := _approve_global(loop, "add", reminder, op, poll_s)):
        return gate
    loop.reminders.append(reminder)
    _persist(loop, reminder, f"add reminder {rid}")
    effect = ("holds a matching action" if reminder.holds
              else f"fires on a matching {kind}")
    return (f"added {rid} ({scope}, {kind}) — /{reminder.regex}/ {effect} from your next "
            "turn on")


def _approve_global(loop, verb: str, target: Reminder, op: dict, poll_s: float) -> str:
    """The blast-radius gate: a global reminder taxes every capable routine, so the user approves
    the write unless the dial says otherwise (an `add` and a revise/delete are dialled apart).
    Returns "" when the write may proceed, else the note why not — an ASK-BACK returns the
    operator's words and asks for the same op again.
    """
    from .askback import still_pending
    from .interact import handle_ask, is_approval
    from .obs_admin import dialog_reply

    ctx = loop.ctx
    if ctx.depth > 0:
        return (f"sub-workflows cannot {verb} a GLOBAL reminder — it binds every routine, so it "
                "is a top-level decision; name it in your summary instead")
    grants = ctx.grants
    if grants is not None and not grants.needs_remind_confirm(creating=verb == "add"):
        return ""
    regex = str(op.get("regex") or target.regex)
    description = str(op.get("description") or target.description)
    reach = str(op.get("reach") or target.reach)
    whom = ("every routine whose action matches" if reach == "universal"
            else "every routine whose settings list it")
    ask = handle_ask(loop, {
        "question": f"Approve {verb} of the GLOBAL consequence reminder '{target.id}'? It holds "
                    f"a matching action in {whom} ({reach}).\npattern: {regex}\n"
                    f"caution: {description}",
        "mode": "blocking", "options": ["approve", "decline"],
        "default": "the global reminder store is NOT changed"}, poll_s,
        qtype="reminder-approval", subject=target.id)
    if ask.get("dialog"):
        # nothing applied: the re-sent op is not a skippable replay, and a finish carrying it
        # must not END over the operator's question (`finishgate.check_finish`)
        loop.reminder_replayed.discard(replay_key("remind", op))
        loop.reminder_asked_back = True
        return f"{verb} of global reminder {target.id}: " + dialog_reply(
            still_pending(ask), "approval", "carry the same `remind` op again (as it was, or "
            "revised in light of their message), your answer in the `say` of the action it "
            "rides", "The curated store is unchanged until they approve.")
    if not ask.get("answered"):
        return (f"the approval for {verb} of global reminder {target.id} is still open — "
                "the store is unchanged; carry on and revisit it once it is settled")
    if not is_approval(ask["answer"]):
        return f"the user declined the {verb} of global reminder {target.id}"
    return ""


# --- persistence: the in-memory set and the two stores, kept in step ---------------------

def _replace(loop, target: Reminder, **changes) -> None:
    updated = Reminder(**{**target.as_record(), **changes})
    loop.reminders = [updated if r.id == target.id else r for r in loop.reminders]


#: The write path lives in `remind_write`, aliased under the names this module's ops call.
_persist = remind_write.persist
_remove = remind_write.remove
_save_local = remind_write.save_local
_commit = remind_write.commit
