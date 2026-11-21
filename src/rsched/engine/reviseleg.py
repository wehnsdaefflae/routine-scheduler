"""The NARROW revise leg: approving a recipe-revision card buys an EDIT, not another run.

D200 resumes the proposing run with its recipe writable (`web/api_recipe_revision.py` →
`engine/revise.py`); the operator approved an edit, so the leg the card arms (`leg: edit`,
`revise.NARROW`) is shaped by the ENGINE to be one:

- **what it may emit** — `LEG_KINDS` only, under the one-leg unlock (`recipe_unlocked`).
- **how long** — `LEG_TURNS`, a backstop, never a pace; the reserved finish turn still applies.
- **what its finish owes** — nothing: no accounting, job checkpoint, verifier or rule assist
  (`finish_rungs.Rung.on_revise_leg`); the rules are cleared at boot, as they govern the TASK.
- **what its ending changes** — only the recipe and the leg's own record. The RUN's verdict
  stays the proposing leg's (`run_status`: status.json, usage status, health stream), the
  leg's summary is APPENDED to `result.md` (`result_text`), outcome/obligations/telemetry are
  not re-run, and its usage row is marked `usage_log.REVISE_LEG` (spend only).

Not narrow: a D198 invitation (the target's next scheduled run also does its task) and the run
view's "editable recipe" checkbox (it carries whatever the operator typed). A narrow leg leaves
a waiting D198 invitation for that next fire.
"""

from __future__ import annotations

from pathlib import Path

#: The kinds a narrow leg may emit. `list_models` rides `actions.ALWAYS_KINDS` like everywhere.
LEG_KINDS = ("read_file", "edit_file", "write_file", "finish", "report")

#: The leg's per-window turn backstop.
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
    return the engine note framing the leg (in place of the follow-up note).
    """
    ctx = loop.ctx
    # no earlier finish: the leg's own status becomes the run's, and the note says "unknown"
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
