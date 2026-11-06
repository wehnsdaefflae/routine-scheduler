"""The NARROW revise leg: approving a recipe-revision card buys an EDIT, not another run.

D200 resumes the run that proposed a revision to its own recipe, with the recipe writable
(`web/api_recipe_revision.py` → `engine/revise.py`). Until this module that leg was a whole run:
it re-oriented, re-read its stages, was offered every kind the routine holds, fired the rules'
pre-finish assists, re-checked its jobs, wrote ledgers and re-edited `phase.json` — work the
PROPOSING leg had already done and accounted for. Measured over the 34 legs on disk that took
a turn (2026-11-06): median 7 turns, 8 of them past 12, the longest 26 (fau 2026-10-27: five
`shell` greps, a memory write and a hand-appended LEDGER entry around fourteen edits), and the
seven legs of fau and moltbook-heartbeat alone cost 98 turns. The operator approved an edit.

So a leg the card arms (the marker's `leg: edit`, `revise.NARROW`) is shaped to be one, and
every part is decided by the ENGINE, never asked of the model:

- **what it may emit** — `LEG_KINDS`: the recipe edit kinds and `finish`/`report`, scoped by
  the existing one-leg unlock (`recipe_unlocked`); nothing else is in its schema.
- **how long it may take** — `LEG_TURNS`, a backstop and never a pace. The edit work in the
  measured legs was 3-8 turns; the long tail was orientation and records this leg no longer
  holds the kinds for, and independent edits may ride one reply (D203). The reserved finish
  turn is unchanged, so an overrun leg still authors its summary and names what it left.
- **what its finish owes** — nothing: no Done-when, goal or outcome accounting, no due-job
  checkpoint, no verifier challenge and no rule assist (`finish_rungs.Rung.on_revise_leg`).
  The proposing leg answered for the run; the leg's rules are cleared at boot for the same
  reason — they govern the TASK, and this leg does none.
- **what its ending changes** — only the recipe and the leg's own record. The RUN's verdict
  stays the proposing leg's (`run_status`): status.json, the usage row's status, the health
  stream (no new not-ok row, and no `run_corrected` row claiming a failed task was redone), and
  `result.md`, to which the leg's summary is APPENDED (`result_text`) — freelance-hunter
  2026-11-02 lost its run summary to a summary of the recipe edit alone. The outcome script,
  the obligations save and the per-run telemetry are not re-run, and the leg's usage row is
  marked (`usage_log.REVISE_LEG`) so the per-run fold adds its spend and keeps the task leg's
  verdict, quality and outcome reading.

What is NOT narrow, and why: a D198 invitation (`rsched/reciperevise.py`) arms the target's
NEXT SCHEDULED run — a full run that also does its task, so its finish owes that task's
accounting and its tools and budget are the task's; and the run view's "editable recipe"
checkbox carries whatever the operator typed, which may ask for more than an edit. Both keep
the full leg. A narrow leg also leaves a waiting D198 invitation for that next fire instead of
taking it, so the leg does exactly what the card approved.
"""

from __future__ import annotations

from pathlib import Path

#: The kinds a narrow leg may emit. `list_models` rides `actions.ALWAYS_KINDS` like everywhere.
LEG_KINDS = ("read_file", "edit_file", "write_file", "finish", "report")

#: The leg's per-window turn backstop (see the module docstring for the measurement).
LEG_TURNS = 12


def configure(loop, *, narrow: bool) -> None:
    """At init: mark the leg and narrow what it may emit. Called after every other widening of
    `allowed_tools` (creation kinds, `decide`), so nothing re-adds a kind behind it.
    """
    loop.revise_leg = narrow
    loop.revise_prior_status = ""
    if narrow:
        loop.allowed_tools = set(LEG_KINDS)


def active(loop) -> bool:
    """Is this loop a narrow revise leg? (`getattr`: a loop double never ran `configure`.)"""
    return bool(getattr(loop, "revise_leg", False))


def boot(loop, prior_finish: dict) -> str:
    """On the resume: keep the run's verdict, drop the task's rules, cap the window — and
    return the engine note that frames the leg (it replaces the follow-up note, whose "finish
    again with the previous result plus what changed" is the opposite of this leg's job).
    """
    ctx = loop.ctx
    # no earlier finish (a leg that never ended) leaves no verdict to keep: the leg's own
    # status is then the run's (`run_status`), and the note says the verdict is unknown
    loop.revise_prior_status = str(prior_finish.get("status") or "")
    loop.assists = []
    budgets = ctx.budgets
    budgets.max_turns = LEG_TURNS if budgets.max_turns < 0 else min(budgets.max_turns, LEG_TURNS)
    if budgets.max_total_turns >= 0:   # the run-spanning cap must not refuse the approved edit
        budgets.max_total_turns = max(budgets.max_total_turns,
                                      ctx.budget_base_turn + budgets.max_turns)
    return note(loop.revise_prior_status or "unknown")


def note(prior_status: str) -> str:
    """The leg's framing note (docs/prompt-anatomy.md pins it)."""
    return (f"this run already ENDED (status {prior_status}) and is resumed for a RECIPE "
            "REVISION only — not a follow-up. Make the edit(s) the message below approves, "
            "read each changed file back once, and finish. This leg holds only the recipe edit "
            f"kinds and a {LEG_TURNS}-turn backstop, owes no accounting and writes no records: "
            f"the run's verdict ({prior_status}) and its result stand, and your summary is "
            "appended to that result — say only what you changed.")


def run_status(loop, leg_status: str) -> str:
    """The status the RUN records for this ending: the proposing leg's, on a narrow leg."""
    return (getattr(loop, "revise_prior_status", "") or leg_status) if active(loop) else leg_status


def result_text(run_dir: Path, summary: str, leg_status: str) -> str:
    """`result.md` after a narrow leg: the run's result as it stood, plus this leg's line."""
    try:
        prior = (run_dir / "result.md").read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError):
        prior = ""
    line = f"**Recipe revision leg** ({leg_status}): {summary.strip()}"
    return f"{prior}\n\n{line}\n" if prior else f"{line}\n"
