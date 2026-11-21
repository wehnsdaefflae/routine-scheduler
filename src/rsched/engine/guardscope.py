"""The SCOPE a run's once-only guards answer for — defined here, once — and their rebuild
when a leg resumes.

Several guards promise to intervene at most ONCE: one hold per reminder and per rule-held
action string (`loop.holds`, engine/hold.py), one fire per rule assist and one finish deferral
across all of them, with the repos found clean remembered beside them (engine/assist.py), one
challenge per claimed line (`loop._challenged`, engine/finishgate.py), one label per reminder
hold and one application of a re-driven finish's reminder payload (engine/remind.py, read back
by engine/remind_ledger.py), one warning before the window's middle is archived
(engine/window.py), one context-window correction per window a failover has substituted DOWN to
(engine/substitution.py). Two COUNTERS answer for the same scope (D245,
engine/resumedcounters.py): per-call failure counts (the repair hint fires at a call's SECOND
failure) and whether repeat-streak shedding has turned the response format off. Each is an
in-memory ledger the loop builds empty, so a resumed leg must rebuild it or every once-only
promise restarts.

**What "once" means:**

- a ROUTINE run — once per RUN. Every leg (a resume after a crash or restart, the operator's
  resume, a parked question answered, a follow-up) is the same run.
- a CONVERSATION — once per REPLY, because each reply is a new task. A leg whose transcript's
  last finish is authored starts a NEW reply and its guards start fresh; any other leg
  continues the reply begun after the last authored finish.

That is the same fact `boot` reads to frame the leg (`leg_after_authored`), so the guards and
the words the model is given about its leg cannot disagree.

**The transcript is the record.** Each ledger is rebuilt from the events inside the scope,
as `history.seen_paths` rebuilds the write-grounding set — never from a side file. A decision
an event must carry is a payload EXTENSION on that event, never a new event type: a hold's
observation names its source by its kind and what it spent (its reminder ids, or `action`);
`assists` names the rule assists whose line an event carried; `undo_point` the repo a write's
pre-action check found clean; `evict_warning` marks the eviction warning's engine note, and
`substitution_floor` the window a substitution note has already corrected the run down to.

Each layer rebuilds its OWN fields (`rebuild(loop, events)`, the counterpart of the
`configure(loop)` that built them — the reminder layer's in `remind_ledger`, beside the live
module): this module owns the scope and nothing of their names.
"""

from __future__ import annotations

from . import runkind


def in_scope(ctx, events: list[dict]) -> list[dict]:
    """The transcript events the once-only guards answer for: all of them for a routine run,
    the current reply's for a conversation (the module docstring says why). `events` is the
    transcript as a resumed leg's boot reads it — before the leg injects anything of its own.
    """
    if not runkind.is_conversation(ctx):
        return events
    finishes = [i for i, e in enumerate(events) if e.get("type") == "finish"]
    if finishes and _authored(events[finishes[-1]]):
        return []           # the last reply was handed back: this leg opens the next one
    opened = max((i for i in finishes if _authored(events[i])), default=-1)
    return events[opened + 1:]


def _authored(event: dict) -> bool:
    return bool((event.get("payload") or {}).get("authored"))


def rebuild(loop, events: list[dict]) -> None:
    """Seed every once-only ledger from the events inside this leg's scope. Called by `boot`
    on a resumed leg, after `configure` built them empty — a fresh run has nothing to read.
    """
    from . import assist, finishgate, hold, remind_ledger, resumedcounters, substitution, window

    scoped = in_scope(loop.ctx, events)
    for layer in (hold, remind_ledger, assist, finishgate, window, substitution,
                  resumedcounters):
        layer.rebuild(loop, scoped)
