"""Clarify instruction — the new-routine wizard's intake.

Turn a raw draft instruction into a clear, self-contained routine instruction by SUGGESTING the
best-fitting workflow pattern (or recommending a fresh one) and MARRYING the task to it: the
clarifying questions are asked with the task overlaid on the chosen pattern's control flow, so the
user resolves ambiguity, contradictions, scope, and the pattern's parameters against how the
routine will actually run. This is a PATTERN acted out one engine action per turn; it drives the
new-routine wizard, never scheduled use.
"""

# --- Parameter contract -------------------------------------------------------------------------
# What this intake works over (provided in its own dir — never resolved at run time):
from routine.params import (
    DRAFT,        # str — the user's raw instruction, this run's INSTRUCTION section
    CANDIDATES,   # the ranked workflow patterns to choose from, written to state/candidates.md
)
from routine.actions import read_file, write_file, ask_user, finish

META = {
    "name": "Clarify instruction",
    "slug": "clarify-instruction",
    "description": "The new-routine wizard's intake — suggest a workflow pattern (or a new one) and "
                   "marry the draft to it into a clear, self-contained routine instruction.",
    "when_to_use": "Internal: drives the new-routine wizard. Takes a raw draft as its instruction, "
                   "picks the fitting workflow pattern (or asks for a new one), asks the user blocking "
                   "questions that overlay the task on that pattern, then writes "
                   "state/wizard_result.json. Not for scheduled use.",
    "version": 5,
    "status": "stable",
    "tags": ["meta", "wizard", "intake"],
    "includes": ["ask-policy"],
    # The engine permits ONLY these action kinds for this workflow — there is nothing to run or
    # discover; the routine's eventual tools are irrelevant to writing its instruction.
    "tools": ["ask_user", "read_file", "write_file", "finish"],
}

PHASES = ["only"]      # one conversation, one run
COMPLETION = (
    "state/wizard_result.json exists with a refined, schedule-free instruction, a chosen workflow "
    "pattern (or a request to generate one), and the parameter values the pattern needs"
)


def run():
    analyze_draft()                 # what's ambiguous / contradictory / missing / outward / done-when
    pattern = choose_pattern()      # read state/candidates.md; pick the best fit, or ask to generate one
    marry(pattern)                  # ≤5 blocking questions overlaying the task on the pattern
    write_result()                  # state/wizard_result.json
    return finish("ok", "the refined instruction + the chosen pattern, in 3-6 lines")


def analyze_draft():
    """The draft is your INSTRUCTION. Hunt for: ambiguity (what exactly is the deliverable? for whom?
    where does it live?), contradictions, missing constraints (budget, language, sources, tone),
    outward acts (does this routine send / publish / spend — what needs a per-item confirmation vs
    standing authorization?), and success criteria (when is it DONE done?).

    OUT OF SCOPE — never ask about, never include: scheduling / frequency, the improvement standards,
    the working directory, and model / endpoint choices. Those are routine CONFIGURATION, set
    separately in the UI. The instruction describes ONLY the task. If the draft names a schedule
    ("every Monday…"), treat it as a hint the user will configure and phrase the task per-run ("each
    run, cover what appeared since the last covered point, tracked in state/") — the task must make
    sense regardless of how often it fires."""


def choose_pattern():
    """`read_file` state/candidates.md — the workflow patterns available for this task, each with its
    control flow (as Python) and its parameter contract (the dummy imports). Pick the ONE whose
    control flow best fits how THIS task should run — that is your suggestion. If none fits well,
    choose instead to GENERATE a new pattern and note the shape it needs (the wizard drafts it).
    Remember the choice for write_result()."""


def marry(pattern):
    """Marry the task to the chosen pattern. Walk its control flow and its parameter contract; for
    each parameter and each branch, decide what the task means there. Resolve obvious defaults
    yourself (ask-policy). Where the task is ambiguous, contradicts the pattern, or leaves a
    parameter unfixed, `ask_user` — mode "blocking", ONE question per turn, at most 5 total: a
    one-sentence situation + the decision + options where sensible. Stop asking as soon as the
    remaining unknowns wouldn't change how the routine runs."""


def write_result():
    """`write_file` state/wizard_result.json:

        {"refined_instruction": "<full ENTRY markdown: the goal, the concrete deliverable and where
            it lives, constraints, what is autonomous vs gated (outward acts), and completion
            criteria — schedule-free and phrased per-run; folds in the user's answers + your stated
            assumptions>",
         "workflow_choice": {"slug": "<chosen pattern slug>"},          # OR, if nothing fit:
         "workflow_choice": {"generate": true, "hint": "<what shape the new pattern needs>"},
         "params": {"<PARAM_NAME>": "<value fixed with the user>", ...},  # the pattern's parameters
         "suggested_slug": "<kebab-case>", "suggested_name": "<short human name>",
         "description": "<one sentence, <=120 chars, saying what this routine does — shown in the UI>",
         "steps": {"<step>.md": "<detailed step instructions>", ...},    # omit or {} unless it splits
         "notes": "<anything the creator should know>"}

    The refined_instruction must make sense to a fresh agent with no memory of this conversation, and
    is the single ENTRY the routine always reads — keep it concise. Split by function into
    steps/<step>.md ONLY when the task has several substantial, separable steps; don't split a simple
    task."""
