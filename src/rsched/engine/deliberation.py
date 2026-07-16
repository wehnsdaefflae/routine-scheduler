"""Deliberation levels — how much of the model's thinking lands ON PAPER.

The persistent prose channel (the `say` field, plus a notes-file discipline at the top
stop) is the only reasoning that survives between turns: thinking tokens are ephemeral and
the message list is append-only, so context the model does not write down does not exist
for any later turn. The user picks a stop per routine/conversation
(config.DELIBERATION_LEVELS); the composer words the say contract from it at boot, and a
mid-run control.json switch re-words it via an engine note at the turn boundary
(engine/control.py). This module is the ONE owner of the per-level wording — composer,
control, and docs all read it here.
"""

from __future__ import annotations

from rsched.config import DEFAULT_DELIBERATION, DELIBERATION_LEVELS

# The say-contract sentence per stop. `standard` is the baseline finding-first contract;
# the two upper stops explicitly license knowledge BEYOND the run (domain conventions,
# base rates, prior art) — the teleological/contextualizing prose that makes narration
# cognitive work, not just status reporting.
_SAY_CONTRACT = {
    "terse": ('The "say" field is ONE terse clause — why this action; spend a full '
              "sentence only on a decision or a surprise."),
    "standard": ('The "say" field is your narration: lead with what the last observation '
                 "taught you, then why this action — a few words for routine steps, 2-3 "
                 "sentences when you decide between options, change direction, or hit a "
                 "surprise."),
    "deliberate": ('The "say" field is your narration: lead with what the last '
                   "observation taught you, add the context that informs it — including "
                   "what you know beyond this run (domain conventions, base rates, prior "
                   "art) when it bears on the step — then why this action. 2-4 sentences; "
                   "give a decision a short paragraph."),
}
_SAY_CONTRACT["think-on-paper"] = _SAY_CONTRACT["deliberate"]

# The standing paragraph only the top stop adds: materialized reasoning in state/notes.md
# before direction-shaping actions. Costs ~1 extra turn per decision; survives compaction
# and reaches the next run.
_STANDING_NOTE = (
    "Deliberation is part of your work product: before an action that shapes the run's "
    "direction (finish, spawn, subtask, ask_user, or entering a new stage), first write "
    "your deliberation to state/notes.md — the options you see, the context and outside "
    "knowledge you bring, your judgment and why — then act from what you wrote, not from "
    "memory."
)


def normalize(level: str) -> str:
    return level if level in DELIBERATION_LEVELS else DEFAULT_DELIBERATION


def say_contract(level: str) -> str:
    """The contract sentence the composer embeds in the harness contract."""
    return _SAY_CONTRACT[normalize(level)]


def standing_note(level: str) -> str:
    """The appended standing paragraph ('' below think-on-paper)."""
    return _STANDING_NOTE if normalize(level) == "think-on-paper" else ""


def switch_note(old: str, new: str) -> str:
    """The engine note a mid-run switch appends so the model learns the new contract."""
    new = normalize(new)
    note = (f"deliberation level switched mid-run: {normalize(old)} → {new}. "
            f"From now on: {say_contract(new)}")
    extra = standing_note(new)
    return f"{note} {extra}" if extra else note
