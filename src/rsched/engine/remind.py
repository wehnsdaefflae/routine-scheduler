"""The consequence-reminder LAYER: pre-execution interception, and the turn-free authoring ops.

A reminder is a caution the model left itself the moment it noticed an action had an
unintended effect (`rsched/reminders.py` owns the store). This file is the runtime half:

- **interception** — before an action executes, its canonical string (`actionschema.canon`) is
  tested against the live set; on a match the action is HELD, not run, and the caution is put
  in front of the model so it decides again. PRE-execution is the whole point: a caution that
  arrives with the observation arrives after the consequence, when nothing can be avoided.
- **the ops** — `remind` (add/revise/delete) and `remind_feedback` (the four-way outcome
  label) ride ANY action as no-turn side fields, exactly like `note`: the moment of realisation
  and the moment of recording are the same turn, or the realisation is lost.

Two rules keep the layer from eating the run:

- **one hold per action string per run.** A held canonical string is remembered, so re-emitting
  the SAME action IS the confirmation to proceed and cannot be held again. The same shape the
  stopping VERIFIER uses (at most one challenge per condition per run) and for the same reason:
  a model and a gate that both refuse to yield would otherwise livelock a run into a dead budget.
- **one hold per action, however many reminders match.** Precedence never multiplies turns.

The turn cost is paid on the INPUT side — selective creation, a precise regex, and the four-way
tally that shows which reminders earn their turns. There is deliberately no cheaper passive tier.
"""

from __future__ import annotations

import json

from .. import reminders as store
from ..reminders import LABEL_HELP, Reminder
from .actionschema import canon


def level_of(grants) -> str:
    """The capability level a set would be read at — "none" for an ungated direct construction."""
    return grants.reminders if grants is not None else "none"


def load(loop) -> list[Reminder]:
    """The live set for this run, read ONCE at construction.

    Once, not per turn: the composed prompt is an append-only caching contract, and a store
    that changes between runs never rewrites a within-run prefix. The in-memory list is kept in
    step with every op this run applies, so it is never stale about its own writes.
    """
    ctx = loop.ctx
    try:
        return store.active(ctx.routine.dir, ctx.server.reminders_home, level_of(ctx.grants))
    except OSError:
        return []


def refresh(loop) -> None:
    """Re-read the set if the capability LEVEL has moved since it was read.

    The read-once rule above has one hole: at level `none` the store is not read at all, so a
    `reminders:*` grant landing mid-run (an access request answered at a turn boundary) leaves
    the in-memory set EMPTY while state/reminders.json is full — and that set is what a
    definition write is rebuilt from, so the run's first `remind` op would rewrite the file
    down to its own single row and drop everything earlier runs had accumulated.

    Comparing the LEVEL rather than the policy object is the point: this is the one thing the
    reload depends on, and `rebuild_policy` (the caller) is the writer of `loop.grants`, so
    reading its own output back as an input would be the wrong dependency.

    A re-read can only ADD: everything this run has already applied is on disk by the time any
    op returns.
    """
    level = level_of(loop.ctx.grants)
    if level != loop.reminders_level:
        loop.reminders = load(loop)
        loop.reminders_level = level


def intercept(loop, action: dict) -> dict | None:
    """Hold the action if a reminder fires on it — or None to let it execute.

    Returns the observation the model reads instead of the action's result. The hold is
    recorded as a `fires` on every matching reminder: the denominator of the tally that decides
    whether the reminder keeps its place.
    """
    if not loop.reminders:
        return None
    rendered = canon(action)
    hits = store.matching(loop.reminders, rendered)
    if not hits or rendered in loop.reminder_held:
        return None
    loop.reminder_held.add(rendered)
    for hit in hits:
        # the tally is disk-owned (store.record); mirror it back so the in-memory set — which
        # is what a later definition write is built from — never carries a stale count
        _replace(loop, hit, stats=store.record(loop.ctx.routine.dir, hit, "fires"))
    # the label this hold is owed, and how long the model has to volunteer it before the
    # engine asks once (a `did`/`didnt` can only be known a turn AFTER the action ran)
    loop.reminder_pending = [h.id for h in hits]
    loop.reminder_nudge = 2
    return {"kind": "reminder_hold", "action": rendered,
            "reminders": [{"id": h.id, "scope": h.scope, "description": h.description}
                          for h in hits]}


def field_problems(action: dict, grants) -> list[str]:
    """Semantic checks for the two side fields, run inside the schema-retry cycle
    (`actions.validate_action`) so a malformed op is corrected and never becomes a turn.

    The capability check lives here rather than in `GrantPolicy.deny`'s kind gate because these
    fields ride EVERY kind, including the always-available ones the kind gate skips.
    """
    problems: list[str] = []
    if (op := action.get("remind")) is not None:
        problems += _remind_problems(op, grants)
    if (fb := action.get("remind_feedback")) is not None:
        # the capability gates the LAYER, not just the write: a run without it has no fires to
        # label, and an accepted label there would answer a channel the docs say is closed
        if grants is not None and (denial := grants.reminder_denial("local")):
            problems.append(denial)
        else:
            problems += _feedback_problems(fb)
    return problems


def _remind_problems(op: object, grants) -> list[str]:
    if not isinstance(op, dict):
        return ['`remind` must be an object: {"op": "add|revise|delete", ...}']
    verb = str(op.get("op") or "")
    if verb not in ("add", "revise", "delete"):
        return ['`remind.op` must be "add", "revise" or "delete"']
    scope = str(op.get("scope") or "local")
    if scope not in store.SCOPES:
        return ['`remind.scope` must be "local" or "global"']
    problems: list[str] = []
    if grants is not None and (denial := grants.reminder_denial(scope)):
        return [denial]
    if verb == "add":
        problems += [p for p in (store.regex_problem(op.get("regex")),
                                 store.description_problem(op.get("description"))) if p]
    else:
        if not str(op.get("id") or "").strip():
            problems.append(f"`remind.op={verb}` needs the `id` of the reminder it changes")
        if op.get("regex") is not None and (p := store.regex_problem(op["regex"])):
            problems.append(p)
        if op.get("description") is not None and (
                p := store.description_problem(op["description"])):
            problems.append(p)
        if verb == "revise" and op.get("regex") is None and op.get("description") is None:
            problems.append("`remind.op=revise` needs a new `regex`, a new `description`, "
                            "or both")
    return problems


def _feedback_problems(fb: object) -> list[str]:
    if not isinstance(fb, dict):
        return ['`remind_feedback` must be an object: {"id": "<reminder id>", '
                '"label": "could_not|would_have|did|didnt"}']
    problems = []
    if not str(fb.get("id") or "").strip():
        problems.append("`remind_feedback.id` must name the reminder whose fire you are "
                        "labelling")
    if str(fb.get("label") or "") not in store.LABELS:
        problems.append(f"`remind_feedback.label` must be one of {list(store.LABELS)} — "
                        f"{LABEL_HELP}")
    return problems


def apply_ops(loop, action: dict, poll_s: float, *, replayable: bool = False) -> str:
    """Apply this action's `remind` / `remind_feedback` fields and return the ENGINE NOTE the
    observation carries — "" when the action had neither.

    Applied AFTER the interception check, never before: a reminder authored on the same turn as
    an action must not be able to hold that very action.

    `replayable` marks a call site the ENGINE itself can re-drive with the same fields — the
    finish path, where every rung of the finish gate hands the SAME finish back for revision
    and the model re-emits it with its side fields intact. The payload is then applied at most
    once per run, which is the rule this codebase already applies to its own re-emissions
    (`reminder_held`: re-emitting a held action is the confirmation, not a second hold; the
    stopping verifier's one challenge per condition). Without it a finish deferred three times
    records one hold's label three times, and the tally the whole layer is justified by —
    `fires` minus the labels — goes negative.
    """
    if replayable and (action.get("remind") or action.get("remind_feedback")):
        key = json.dumps([action.get("remind"), action.get("remind_feedback")], sort_keys=True)
        if key in loop.reminder_replayed:
            return ""
        loop.reminder_replayed.add(key)
    notes = []
    if action.get("remind_feedback"):
        notes.append(_apply_feedback(loop, action["remind_feedback"]))
    if action.get("remind"):
        notes.append(_apply_op(loop, action["remind"], poll_s))
    notes.append(_label_nudge(loop, action))
    lines = [n for n in notes if n]
    return ("\n" + "\n".join(f"[REMINDERS: {n}]" for n in lines)) if lines else ""


def _label_nudge(loop, action: dict) -> str:
    """Ask ONCE for a label a fire is still owed.

    Not a hard requirement: `remind_feedback` rides every kind, so rejecting an action that
    omits it would put a bookkeeping field in the way of the work — and the schema-storm guard
    fails a run whose turns keep needing retries. So the hold demands the label, and this asks
    a second time, two turns later, once the `did`/`didnt` case has had the turn it needs to
    know its own outcome. What stays unlabelled after that is visible in the tally as
    `fires` minus the labels, which is itself the signal that a reminder is not being read.
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
    rid = str(fb.get("id") or "")
    label = str(fb.get("label") or "")
    target = store.find(loop.reminders, rid)
    if target is None:
        return (f"no reminder {rid!r} is live for this run, so the {label!r} label was not "
                "recorded — the ids are in the hold you are answering")
    tally = store.record(loop.ctx.routine.dir, target, label)
    _replace(loop, target, stats=tally)
    counts = " / ".join(f"{n} {f}" for f in store.LABELS if (n := tally.get(f)))
    return f"{rid} labelled {label} ({tally.get('fires', 0)} fires: {counts or 'none labelled'})"


def _apply_op(loop, op: dict, poll_s: float) -> str:
    verb = str(op.get("op") or "")
    scope = str(op.get("scope") or "local")
    if verb == "add":
        return _add(loop, op, scope, poll_s)
    rid = str(op.get("id") or "")
    target = store.find(loop.reminders, rid)
    if target is None:
        return (f"no reminder {rid!r} is live for this run — nothing was {verb}d; the live ids "
                "are in the holds you have seen")
    if scope != target.scope and op.get("scope") is not None:
        return (f"{rid} is a {target.scope} reminder and a {verb} cannot move it — to promote a "
                "proven local reminder, `add` it with scope global (its evidence is per-routine "
                "and starts fresh there), then delete the local one")
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
                       description=str(op.get("description") or target.description))
    _replace(loop, target, regex=revised.regex, description=revised.description)
    _persist(loop, revised, f"revise reminder {rid}")
    return f"{rid} revised ({target.scope}) — now /{revised.regex}/ {revised.description}"


def _add(loop, op: dict, scope: str, poll_s: float) -> str:
    ctx = loop.ctx
    local = [r for r in loop.reminders if r.scope == "local"]
    if scope == "local" and len(local) >= store.MAX_LOCAL:
        return (f"this routine already holds {store.MAX_LOCAL} local reminders, the cap — "
                "delete one that its tally shows is not earning its turns before adding another")
    # Scoped to the SAME store on purpose. A local reminder shadowing a global one with the
    # same pattern is the union's designed precedence, and PROMOTION is exactly that overlap
    # for one turn — `add` the global copy, then delete the local one. Checking across both
    # stores made the engine's own promotion instructions impossible to follow.
    if any(r.regex == op.get("regex") and r.scope == scope for r in loop.reminders):
        return (f"a {scope} reminder with that exact pattern is already live — revise it "
                "instead of adding a second one that would hold the same actions")
    rid = store.new_id(ctx.run_ts, {r.id for r in loop.reminders})
    reminder = Reminder(id=rid, regex=str(op["regex"]), description=str(op["description"]),
                        scope=scope, created_run=ctx.run_id, stats=store.blank_stats())
    if scope == "global" and (gate := _approve_global(loop, "add", reminder, op, poll_s)):
        return gate
    loop.reminders.append(reminder)
    _persist(loop, reminder, f"add reminder {rid}")
    return (f"added {rid} ({scope}) — /{reminder.regex}/ holds a matching action from your next "
            "turn on")


def _approve_global(loop, verb: str, target: Reminder, op: dict, poll_s: float) -> str:
    """The blast-radius gate: a global reminder taxes EVERY capable routine at its next run,
    silently, so the user approves the write unless the dial says otherwise. Returns "" when
    the write may proceed, or the note explaining why it did not.

    `creations` splits the ladder where the blast radius does: a NEW global reminder starts
    holding actions in routines that never asked for it; revising or deleting one only changes
    something the user already approved.
    """
    from .interact import handle_ask, is_approval

    ctx = loop.ctx
    if ctx.depth > 0:
        return (f"sub-workflows cannot {verb} a GLOBAL reminder — it binds every routine, so it "
                "is a top-level decision; name it in your summary instead")
    grants = ctx.grants
    if grants is not None and not grants.needs_remind_confirm(creating=verb == "add"):
        return ""
    regex = str(op.get("regex") or target.regex)
    description = str(op.get("description") or target.description)
    ask = handle_ask(loop, {
        "question": f"Approve {verb} of the GLOBAL consequence reminder '{target.id}'? It holds "
                    f"every matching action in every routine holding the reminders capability "
                    f"at global.\npattern: {regex}\ncaution: {description}",
        "mode": "blocking", "options": ["approve", "decline"],
        "default": "the global reminder store is NOT changed"}, poll_s,
        qtype="reminder-approval")
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


def _remove(loop, target: Reminder) -> None:
    loop.reminders = [r for r in loop.reminders if r.id != target.id]
    if target.scope == "global":
        store.delete_global(loop.ctx.server.reminders_home, target.id)
    else:
        _save_local(loop)


def _persist(loop, reminder: Reminder, message: str) -> None:
    """Write ONE reminder to the store its scope names — the local file (rewritten whole, it is
    small and single-writer) or its own file in the library, committed like any library write.
    """
    if reminder.scope == "global":
        home = loop.ctx.server.reminders_home
        home.mkdir(parents=True, exist_ok=True)
        store.write_global(home, reminder)
        _commit(loop, message, reminder)
    else:
        _save_local(loop)


def _save_local(loop) -> None:
    """Rewrite the local store: DEFINITIONS from memory, TALLIES from disk.

    The split is load-bearing. `store.record` is the only writer of a tally and does its own
    read-modify-write, so DISK is authoritative for stats; this run's ops are what changed the
    definitions, so MEMORY is authoritative for those. Taking both halves from memory rolled
    every `fires` this run recorded back to its boot-time value, because a frozen in-memory
    `Reminder` never saw the increment — the global tallies were already merged this way, and
    the local half needed the same treatment.
    """
    on_disk, gstats = store.load_local(loop.ctx.routine.dir)
    tallies = {r.id: r.stats for r in on_disk}
    merged = [Reminder(**{**r.as_record(), "stats": tallies[r.id]}) if r.id in tallies else r
              for r in loop.reminders]
    store.save_local(loop.ctx.routine.dir, merged, gstats)


def _commit(loop, message: str, reminder: Reminder) -> None:
    from .. import libgit

    libgit.commit(loop.ctx.server.libraries_home, message,
                  paths=[store.global_rel(reminder.id)])
