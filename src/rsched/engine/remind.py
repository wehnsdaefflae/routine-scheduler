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
  claim VERIFIER uses (at most one challenge per claimed line per run) and for the same reason:
  a model and a gate that both refuse to yield would otherwise livelock a run into a dead budget.
- **one hold per action, however many reminders match.** Precedence never multiplies turns.

"Per run" is the GUARD SCOPE (engine/guardscope.py) — every leg of a routine run, the current
reply of a conversation — and this layer's own ledgers follow the hold ledger across a resume
(`remind_ledger`): a hold from before a restart is still owed its one label after it.

The turn cost is paid on the INPUT side — selective creation, a precise regex, and the four-way
tally that shows which reminders earn their turns; no cheaper passive tier exists.

A reminder is ONE CONFIGURATION OF A HOOK (D152): trigger `match` over an action, a result,
the turn's prose or a turn moment; addressee this routine's own future runs or another
routine; delivery `hold_before_action` for the holding kind and `turn_boundary` for the rest.
Each write is mirrored into the one hook store (`engine/hookmirror`, `rsched/hooks`), which
owns that vocabulary; this layer stays the mechanism.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import reminders as store
from ..reminders import LABEL_HELP, Reminder
from . import hold as hold_seam
from . import hookmirror, remind_write

#: This layer's name in the shared hold ledger. Each source gets its own
#: one-hold-per-action budget: keyed on the bare action string, a reminder hold would
#: silently spend the rule layer's only hold on that string, and its caution would
#: never be seen.
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
    # the `turn:answer` watermark: how many user replies had arrived when this layer last
    # looked, so the moment is the ARRIVAL EDGE rather than "a reply exists" (the same shape
    # `assist.at_boundary` uses for its own arrival edge)
    loop.reminder_user_replies = int(getattr(loop.ctx, "user_replies", 0) or 0)


def replay_key(field: str, payload: object) -> str:
    """One side field's payload as the replay ledger stores it — what a re-driven finish is
    deduplicated by, field by field (`apply_ops`, its resume rebuild in `remind_ledger`, and the
    ask-back's withdrawal in `_approve_global`).
    """
    return json.dumps([field, payload], sort_keys=True)


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
        return store.active(ctx.routine.dir, ctx.server.reminders_home, level_of(ctx.grants),
                            list(getattr(ctx.routine, "shared_reminders", None) or []), ctx.run_id)
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


def hold(loop, action: dict, rendered: str) -> dict | None:  # noqa: ARG001 — the seam calls every source with ONE signature; this layer matches on the rendering alone
    """This layer's answer for the shared pre-execution seam (`engine/hold.py`): the
    observation the model reads instead of the action's result, or None to let it execute.

    `rendered` is the canonical action string, computed ONCE by the seam and shared with
    the other source, so the two can never disagree about what the action was. The hold
    is recorded as a `fires` on every matching reminder: the denominator of the tally
    that decides whether the reminder keeps its place.
    """
    if not loop.reminders:
        return None
    hits = store.matching(loop.reminders, rendered, kind="action")
    if not hits or hold_seam.held_before(loop, SOURCE, rendered):
        return None
    hold_seam.mark_held(loop, SOURCE, rendered)
    _count_fires(loop, hits)
    # the tally rides the observation: `store.looks_too_broad` is what turns a hold into the
    # moment its own evidence is readable, and obs_hold renders the line
    live = {h.id: h for h in loop.reminders}
    return {"kind": "reminder_hold", "action": rendered,
            "reminders": [{"id": h.id, "scope": h.scope, "description": h.description,
                           "owner": h.owner,
                           "stats": dict(live.get(h.id, h).stats)}
                          for h in hits]}


def _count_fires(loop, hits: list[Reminder]) -> None:
    """Record a fire on every reminder that just matched, and arm the label it is owed.

    Shared by every fire point so the tally means one thing whatever the trigger kind was: the
    denominator the four-way labels are read against. The tally is disk-owned
    (`store.record`); the result is mirrored back into the in-memory set, which is what a later
    definition write is rebuilt from, so it never carries a stale count.
    """
    for hit in hits:
        _replace(loop, hit, stats=store.record(loop.ctx.routine.dir, hit, "fires"))
        loop.reminder_owed[hit.id] = loop.reminder_owed.get(hit.id, 0) + 1
    # the label this fire is owed, and how long the model has to volunteer it before the
    # engine asks once (a `did`/`didnt` can only be known a turn AFTER the action ran)
    loop.reminder_pending = [h.id for h in hits]
    loop.reminder_nudge = 2


def _fired_line(hit: Reminder, target_name: str) -> str:
    """One line, one shape, for every non-holding kind — what fired, on what, and the caution.

    A FOREIGN hook says so and names its owner, because the disposition choice (keep / mute /
    remove) is only the target's to make if the target can tell the hook is not its own.
    """
    whose = f"set by `{hit.owner}`" if hit.foreign else "your own caution"
    tail = (f" ({hit.owner} set this: carry remind_feedback with disposition keep/mute/remove to "
            f"decide its fate, and {LABEL_HELP})" if hit.foreign
            else f" (label it with remind_feedback: {LABEL_HELP})")
    return f"[REMINDER {hit.id} — {whose}, on this turn's {target_name}] {hit.description}{tail}"


def at_observation(loop, action: dict, obs: dict) -> str:
    """The tail appended to an observation for the kinds that CANNOT hold — "" when nothing
    fired. Costs no turn.

    `result`, `prose` and the `turn:*` moments all describe something that has already
    happened, so there is nothing to prevent and no reason to spend a turn; they ride the
    observation exactly as an observation-moment rule assist does (`engine/assist`), which is
    the shape this codebase already uses for "what just came back".

    The targets, each a plain string so one regex engine serves every kind:
    - `result` — the observation as the model is shown it (`format_observation` is applied by
      the caller for display; the match target is the same text, so a pattern can name an exit
      code or an error the model will read);
    - `prose` — the turn's own `say`;
    - `turn:finish` / `turn:question` / `turn:error` / `turn:first` — the moment's own name plus
      that same observation text, so a pattern may anchor on the moment alone (`^turn:finish`)
      or on something inside it.
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
        for hit in store.matching(loop.reminders, target, kind=kind):
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
    fired = store.matching(loop.reminders, target, kind="turn:finish")
    if not fired:
        return ""
    _count_fires(loop, fired)
    return "\n" + "\n".join(_fired_line(h, "finish turn") for h in fired)


def apply_ops(loop, action: dict, poll_s: float, *, replayable: bool = False) -> str:
    """Apply this action's `remind` / `remind_feedback` fields and return the ENGINE NOTE the
    observation carries — "" when the action had neither.

    Applied AFTER the interception check, never before: a reminder authored on the same turn as
    an action must not be able to hold that very action.

    `replayable` marks a call site the ENGINE itself can re-drive with the same fields — the
    finish path, where every rung of the finish gate hands the SAME finish back for revision
    and the model re-emits it with its side fields intact. Each field's payload is then applied
    at most once per run (per guard scope, and across a resume: `remind_ledger`), which is the
    rule this codebase already applies to its own
    re-emissions (`loop.holds`, engine/hold.py: re-emitting a held action is the confirmation,
    not a second hold; the claim verifier's one challenge per claimed line). Without it a
    finish deferred three times records one hold's label three times, and the tally the whole
    layer is justified by — `fires` minus the labels — goes negative. Keyed per FIELD, because
    an op the operator asked back on was not applied (`_approve_global` withdraws its key): the
    finish that carries it again is the re-submission they are owed, and the label beside it
    must still not count twice.
    """
    loop.reminder_asked_back = False     # set by `_approve_global`, read by the finish gate
    fields = [f for f in ("remind_feedback", "remind") if action.get(f)]
    if replayable and fields:
        fields = [f for f in fields if replay_key(f, action[f]) not in loop.reminder_replayed]
        if not fields:
            return ""
        loop.reminder_replayed.update(replay_key(f, action[f]) for f in fields)
    notes = []
    if "remind_feedback" in fields:
        notes.append(_apply_feedback(loop, action["remind_feedback"]))
    if "remind" in fields:
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
    """Record one label — for a hold of this reminder, in this run, not labelled yet. A label
    with no hold behind it is evidence about nothing: before this, one run labelled a single
    reminder 75 times and its tally read 5 fires and 77 `would_have`.
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
    """The TARGET's say over a hook ANOTHER routine set on it (D152-C step 2).

    Applied at the fire, on the same field as the label, because that is the one moment the
    hook's usefulness is observable. It outranks its owner: `remove` also records the refusal,
    so the owner's next run cannot simply set the same pattern again.

    A disposition on a hook the routine wrote ITSELF is refused rather than silently applied —
    its own reminder is revised or deleted with a `remind` op, and conflating the two would make
    `mute` a second, undocumented way to disable one.
    """
    if disposition is None:
        return ""
    if not target.foreign:
        return (f"; no disposition applied — {target.id} is your own reminder, not one another "
                "routine set on you: revise or delete it with a `remind` op")
    applied = store.dispose(loop.ctx.routine.dir, target.id, str(disposition), loop.ctx.run_id)
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
    """Drop a disposed-of hook from this run's live set. The STORE write is `store.dispose`'s;
    this only keeps the in-memory set — which a later definition write is rebuilt from — in step
    with it, the same split `_save_local` documents.
    """
    loop.reminders = [r for r in loop.reminders if r.id != target.id]


def _resolve_target(loop, op: dict, rid: str) -> Reminder | str:
    """The reminder a `revise` or `delete` names — the live set first, the SHARED STORE second.

    The live set alone could not answer for a curated row this routine does not hold (D186,
    R2530). A `reach: listed` global reminder is in NO non-listing routine's live set by
    construction, so a curator that does not list it could never name an id `delete` would
    accept: the refusal was structural, not a lookup miss, and the row was permanent unless the
    operator removed the file by hand. The reach gate decides when a row FIRES, not who may
    curate it — so an op that says `scope: global` resolves against the library.

    The fallback is deliberately narrow: only when the op NAMES global (`scope` is not left to
    default), and only for a row the library actually holds. Everything about AUTHORITY stays
    where it was — the scope-mismatch guard, `grants.reminder_denial`, the global approval step
    and the commit all run on the returned target exactly as before.

    Returns the reminder, or the refusal note. The two misses stay textually distinguishable so
    a caller can tell a typo from a reach gate: "is not in the shared store" names the library,
    "is live for this run" names this run's set.
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
    resolved = _resolve_target(loop, op, rid)
    if isinstance(resolved, str):
        return resolved
    target = resolved
    if scope != target.scope and op.get("scope") is not None:
        return (f"{rid} is a {target.scope} reminder and a {verb} cannot move it — to promote a "
                "proven local reminder, `add` it with scope global (its evidence is per-routine "
                "and starts fresh there), then delete the local one")
    # The write gate asked the dial about the scope the op NAMED, and a revise or delete need
    # not name one (it defaults to local) — while the store it writes is the TARGET's. Without
    # asking again here, a routine at `local` could rewrite or remove a curated reminder in
    # the library every routine reads, which is the curator's setting alone.
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
    """Set a hook in ANOTHER routine's store (D152-C step 2) — the note the run reads back.

    The authority a `report` has, with the target's say added: the hook lands in the target's own
    local store, its runs meet it from the next one on, and the TARGET disposes of it on a fire
    (`reminders.dispose`). The sender does not hold it, so it is not added to this run's live set
    and never fires here — a caution about someone else's actions could not fire here anyway.

    Every refusal is the target's: its cap, a pattern it already holds, or one it removed before.
    They come back as the note rather than as an exception, because a refused hook is not a
    failed turn — the run reads why and carries on.
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
    # The store this reminder joins, as (id, pattern) pairs. For the curated store that is every
    # record in the library, not only the ones that reach THIS routine: a `listed` reminder it
    # does not list still owns its id and its pattern there — and an id picked against the live
    # set alone could be that one's, which `write_global` then overwrote.
    joined = ([(rec["id"], rec["regex"]) for rec in store.records(ctx.server.reminders_home)]
              if scope == "global" else [(r.id, r.regex) for r in local])
    # Scoped to the SAME store on purpose. A local reminder shadowing a global one with the
    # same pattern is the union's designed precedence, and PROMOTION is exactly that overlap
    # for one turn — `add` the global copy, then delete the local one. Checking across both
    # stores made the engine's own promotion instructions impossible to follow.
    kind = store.read_kind(op.get("kind"))
    # the duplicate test is per (pattern, KIND): the same pattern on a different trigger is a
    # different consequence class, exactly as the union's dedupe key says. The scan stays over
    # the SAME store's live set (`joined` carries that store's ids for the collision check).
    if any(r.regex == op.get("regex") and r.kind == kind and r.scope == scope
           for r in loop.reminders):
        return (f"a {scope} {kind} reminder with that exact pattern is already live — revise it "
                "instead of adding a second one that would fire on the same moments")
    rid = store.new_id(ctx.run_ts, {r.id for r in loop.reminders} | {i for i, _ in joined})
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
    """The blast-radius gate: a global reminder taxes EVERY capable routine at its next run,
    silently, so the user approves the write unless the dial says otherwise. Returns "" when
    the write may proceed, or the note explaining why it did not.

    `creations` splits the ladder where the blast radius does: a NEW global reminder starts
    holding actions in routines that never asked for it; revising or deleting one only changes
    something the user already approved.

    An ASK-BACK on the approval comes back as the note itself: the operator's words, and the
    instruction to carry the same op again — the decision's subject is the reminder id the
    question names, so that re-submission replaces the open record.
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
        # Nothing was applied, so carrying the same op again is the re-submission the operator
        # is owed — never a replay the finish path may skip (`apply_ops`).
        loop.reminder_replayed.discard(replay_key("remind", op))
        # …and a finish that carried it must not END over the operator's question: the note
        # rides an observation, and a finish that stands has none (`finishgate.check_finish`)
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


def _remove(loop, target: Reminder) -> None:
    loop.reminders = [r for r in loop.reminders if r.id != target.id]
    if target.scope == "global":
        store.delete_global(loop.ctx.server.reminders_home, target.id)
    else:
        _save_local(loop)


#: The write path lives in `remind_write` — `persist` (which store a definition goes to, plus
#: the hook mirror), `save_local` (the definitions-from-memory / tallies-from-disk rewrite) and
#: `commit` (the library write). Aliased under the names this module's ops already call, so the
#: extraction moved code and nothing else.
_persist = remind_write.persist
_save_local = remind_write.save_local
_commit = remind_write.commit
