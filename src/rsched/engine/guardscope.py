"""The SCOPE a run's once-only guards answer for — defined here, once — and their rebuild
when a leg resumes.

Several guards promise to intervene at most ONCE: one hold per source and action string
(`loop.holds`, engine/hold.py), one fire per rule assist and one finish deferral across all of
them, with the repos found clean remembered beside them (engine/assist.py), one challenge per
claimed line (`loop._challenged`, engine/finishgate.py), one label per reminder hold and one
application of a re-driven finish's reminder payload (engine/remind.py, read back by
engine/remind_ledger.py), one warning before the window's middle is archived
(engine/window.py). Each is an in-memory ledger the loop
builds at construction — so every resumed leg used to start them EMPTY: a hold the run had
already confirmed stopped the same action again after a restart, a finish deferral recurred,
the verifier argued a line it had already conceded, and a repo the run had found clean read
as dirty with the run's own edits.

**What "once" means** (operator decision, 2026-10-01):

- a ROUTINE run — once per RUN. Every leg is the same run: a resume after a crash or a
  restart, the operator's resume, a parked question answered after its process died, a
  follow-up on a finished run.
- a CONVERSATION — once per REPLY, because each reply is a new task. A reply ends in the
  authored finish that IS its chat message (docs/conversations.md), so a leg whose
  transcript's last finish is authored starts a NEW reply — the user's message it is about
  to inject opens it — and its guards start fresh. Any other leg continues the reply that
  began after the last authored finish: one an engine-written finish cut off (a crash, a
  restart, an abort, a budget the engine had to end), or the first reply, which has no finish
  before it.

That is the fact `boot` already reads to frame the leg (`leg_after_authored`: "this run
already ENDED — a follow-up" against "interrupted and now RESUMED"), so the guards and the
words the model is given about its leg cannot disagree about which reply it is in.

**The transcript is the record.** Each ledger is rebuilt from the events inside the scope,
exactly as `history.seen_paths` rebuilds the write-grounding set — never from a side file the
transcript could contradict. Where an event did not record the decision, it now does, as a
payload EXTENSION on the event that carried the intervention, never a new event type: a hold's
observation names its source by its kind; `assists` names the rule assists whose line an
event carried; `undo_point` the repo a write's pre-action check found clean; `evict_warning`
marks the eviction warning's engine note.

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
    from . import assist, finishgate, hold, remind_ledger, window

    scoped = in_scope(loop.ctx, events)
    for layer in (hold, remind_ledger, assist, finishgate, window):
        layer.rebuild(loop, scoped)
