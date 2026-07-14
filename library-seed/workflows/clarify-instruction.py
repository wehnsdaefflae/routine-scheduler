"""Clarify instruction — the new-routine wizard's intake.

Applied (decomposed) to a RAW DRAFT instruction, this pattern becomes a throwaway "clarification
routine". Running it interrogates the draft into a clear, self-contained routine instruction, by
SUGGESTING the best-fitting workflow pattern (or a fresh one) and MARRYING the task to it — asking
the user questions that overlay the task on how the routine will actually run.

IMPORTANT: you are only refining the WORDING of the instruction. You do NOT perform the task the
draft describes. Your sole product is state/wizard_result.json.
"""

# --- Parameter contract -------------------------------------------------------------------------
from routine.params import (
    DRAFT,        # str — the raw draft instruction to clarify (this run's INSTRUCTION)
    CANDIDATES,   # the ranked workflow patterns to choose from, written to state/candidates.md
)
from routine.actions import read_file, write_file, ask_user, finish

META = {
    "name": "Clarify instruction",
    "slug": "clarify-instruction",
    "description": "The new-routine wizard's intake — suggest a workflow pattern (or a new one) and "
                   "marry the draft to it into a clear, self-contained routine instruction.",
    "when_to_use": "Internal: drives the new-routine wizard. Applied to a raw draft, it picks the "
                   "fitting workflow pattern, asks the user blocking questions that overlay the task "
                   "on that pattern, then writes state/wizard_result.json. Not for scheduled use.",
    "version": 7,
    "status": "stable",
    "tags": ["meta", "wizard", "intake"],
    "includes": ["ask-policy"],
    # Only these action kinds are permitted — there is nothing to run or discover, only to clarify.
    "tools": ["ask_user", "read_file", "write_file", "finish"],
}

PHASES = ["only"]      # one conversation, one run
COMPLETION = (
    "state/wizard_result.json exists with a refined, schedule-free instruction, a chosen workflow "
    "pattern (or a request to generate one), and the parameter values the pattern needs"
)


def main():
    analyze_draft()                 # what's ambiguous / contradictory / missing / outward / done-when
    pattern = choose_pattern()      # read state/candidates.md; pick the best fit, or ask to generate one
    marry(pattern)                  # ≤5 blocking questions overlaying the task on the pattern
    write_result()                  # state/wizard_result.json — your only product
    return finish("ok", "the refined instruction + the chosen pattern, in 3-6 lines")


def analyze_draft():
    """The DRAFT is your INSTRUCTION — the raw text to clarify (you do not perform it). Hunt for:
    ambiguity (what exactly is the deliverable? for whom? where does it live?), contradictions,
    missing constraints (budget, language, sources, tone), outward acts (does this routine send /
    publish / spend — what needs a per-item confirmation vs standing authorization?), and success
    criteria (when is it DONE done?).

    OUT OF SCOPE — never ask about, never include: scheduling / frequency, the improvement standards,
    the working directory, and model / endpoint choices. Those are routine CONFIGURATION, set
    separately in the UI. The instruction describes ONLY the task. If the draft names a schedule
    ("every Monday…"), treat it as a hint and phrase the task per-run ("each run, cover what appeared
    since the last covered point, tracked in state/") — it must make sense regardless of cadence."""


def choose_pattern():
    """`read_file` state/candidates.md — the workflow patterns available for this task, each with its
    control flow and its parameter contract. Pick the ONE whose control flow best fits how THIS task
    should run — that is your suggestion. If none fits well, choose instead to GENERATE a new pattern
    and note the shape it needs. Remember the choice for write_result()."""


def marry(pattern):
    """Marry the task to the chosen pattern. For each of the pattern's parameters and each branch of
    its control flow, decide what the task means there. Resolve obvious defaults yourself (ask-policy).
    Where the task is ambiguous, contradicts the pattern, or leaves a parameter unfixed, `ask_user` —
    mode "blocking", ONE question per turn, at most 5 total: a one-sentence situation + the decision +
    options where sensible. Stop asking once the remaining unknowns wouldn't change how the routine
    runs."""


def write_result():
    """Emit ONE `write_file` action — and NOTHING else in that action. Its only fields are `kind`,
    `say`, `path`, `content`:
      - `path`: `state/wizard_result.json`
      - `content`: a JSON STRING with exactly these keys:
        {"refined_instruction": "<the full ENTRY markdown: goal, the concrete deliverable and where
            it lives, constraints, what is autonomous vs gated (outward acts), completion criteria —
            schedule-free and phrased per-run; folds in the user's answers + your assumptions>",
         "workflow_choice": {"slug": "<chosen pattern slug>"},          # OR {"generate": true, "hint": "..."}
         "params": {"<PARAM_NAME>": "<value fixed with the user>", ...},  # the pattern's parameters
         "suggested_slug": "<kebab-case>", "suggested_name": "<short human name>",
         "description": "<one sentence, ≤120 chars, what this routine does — shown in the UI>",
         "steps": {"<step>.md": "<detail>", ...},                        # omit or {} unless it splits
         "notes": "<anything the creator should know>"}

    Everything above (refined_instruction, workflow_choice, params, …) goes INSIDE the `content`
    string — NEVER as top-level fields of the action. This is not a `finish`; the action has no
    `status`/`summary`/`workflow` fields. The refined_instruction must make sense to a fresh agent
    with no memory of this conversation; split into steps/<step>.md files only for genuinely
    separable multi-step tasks.

    OWNERSHIP RULE — the instruction is the TASK, nothing else. Cross-cutting conduct is owned
    by TRAITS (practice modules adapted into the routine at creation: asking policy, after-run
    improvement passes, util and web-research discipline) and capabilities by user-set
    PERMISSIONS (communication channels, util authoring, previous-run access): the
    refined_instruction must contain NONE of it, and must not assume any trait or permission is
    present. If the user's draft mixes conduct into the task ("message me on discord when...",
    "improve your own prompt after each run"), do not copy it into the instruction — flag it in
    `notes` as a trait/permission choice for the wizard. Conduct text baked into the instruction
    would keep acting after the user changes the routine's setup, which breaks their control
    surface."""


if __name__ == "__main__":
    main()
