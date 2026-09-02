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
    DRAFT,        # str — the raw draft to clarify (this run's INSTRUCTION; persisted VERBATIM
                  # at instruction.md in the working dir — read_file THAT for the exact text;
                  # no params.* file ever exists at run time)
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
    "version": 12,
    "tags": ["meta", "wizard", "intake"],
    "includes": ["ask-policy"],
    # The deliverable must survive decomposition: applied to a draft that itself describes a
    # routine, the generator has been observed building THAT routine instead of the clarify
    # flow. A pinned path missing from the tailored files makes decompose fall back to this
    # pattern rendered verbatim (see workflows.adapt.decompose).
    "pin": ["state/wizard_result.json"],
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
    """The DRAFT is your INSTRUCTION — the raw text to clarify (you do not perform it); the
    verbatim draft also sits on disk at `instruction.md` in the working dir, so `read_file
    instruction.md` recovers it exactly — never look for a `params.*` file. Hunt for:
    ambiguity (what exactly is the deliverable? for whom? where does it live?), contradictions,
    missing constraints (budget, language, sources, tone), outward acts (does this routine send /
    publish / spend — what needs a per-item confirmation vs standing authorization?), and success
    criteria (when is it DONE done?).

    TWO ANSWERS ARE MANDATORY — the intake is not finished without them, whatever else the draft
    settles:
      1. **What the routine PRODUCES each run** — the concrete artefact, named, and where it lands.
         Not the activity ("monitors the feed") but the output ("appends new entries to
         state/seen.md and writes a digest to the report").
      2. **What DONE looks like for ONE run** — the observable condition under which this run is
         finished, checkable without asking anyone.
    A draft that does not already fix both is not clarified yet. Carry whichever is missing into
    marry() as a blocking question — those two are asked FIRST and are never resolved by assuming
    a sensible default. A routine born without them cannot tell a finished run from an abandoned
    one, and every later run inherits the ambiguity.

    INGEST/OUTBOUND SPLIT — when the task BOTH ingests/processes signal (reads sources, updates
    state, computes) AND sends outbound communication (email / messages / publishing), especially
    if the routine will run inside a routine GROUP, offer the user (a blocking question in marry())
    the option to SPLIT it into TWO routines: an ingestion+processing routine and an
    outbound-communication routine placed in the same group. The payoff: grouped members can all
    ingest/process first and all communicate after (same order), so a member's outbound can depend
    on another member's freshly-processed state instead of waiting a whole cadence for a reaction.
    If the user takes the split, write_result() emits the two routines' instructions (each
    self-contained); if not, keep it as one. (Operator standing rule, 2026-08-05, R214.3.)

    RECIPE vs PROCEDURE — a routine has a prose recipe (LLM-interpreted judgment) and may also carry
    its OWN Python under scripts/ (deterministic mechanism the recipe calls). At creation, judge which
    parts of the task are judgment-free and repeated identically every run — fetching / polling (mail,
    feeds, APIs), parsing or reformatting structured data, arithmetic on updated data,
    filtering / sorting / dedup, threshold checks, assembling a fixed artifact — and mark those for the
    routine's own scripts/ (the recipe stays the single interpreter and delegates to them), while
    genuinely generative work (drafting prose, evaluating fit, deciding what matters) stays in the
    recipe. Record the intended split in write_result()'s notes so the new routine is BORN with a
    sensible recipe/procedure distribution instead of doing mechanism by hand each run. A capability
    reusable across routines is a shared util, not this routine's script. (Operator standing rule,
    2026-08-12, via R305.)

    OUT OF SCOPE — never ask about, never include: scheduling / frequency, the improvement standards,
    the working directory, and model / endpoint choices. Those are routine CONFIGURATION, set
    separately in the UI. The instruction describes ONLY the task. If the draft names a schedule
    ("every Monday…"), treat it as a hint and phrase the task per-run ("each run, cover what appeared
    since the last covered point, tracked in state/") — it must make sense regardless of cadence."""


def choose_pattern():
    """`read_file` state/candidates.md — the workflow patterns available for this task, each with its
    control flow and its parameter contract. Pick the ONE whose control flow best fits how THIS task
    should run — that is your suggestion. If none fits well, choose instead to GENERATE a new pattern
    and note the shape it needs. Remember the choice for write_result().

    The choice must be MADE, not defaulted to. Name the runner-up as well and why the winner fits
    this task better — one sentence each, into write_result()'s `notes`. A general-purpose pattern
    is a legitimate answer only when you can say what it beats; picked silently it is just the
    absence of a decision, and the routine then runs for months on a control flow nobody chose."""


def marry(pattern):
    """Marry the task to the chosen pattern. For each of the pattern's parameters and each branch of
    its control flow, decide what the task means there. Resolve obvious defaults yourself (ask-policy).
    Where the task is ambiguous, contradicts the pattern, or leaves a parameter unfixed, `ask_user` —
    mode "blocking", ONE question per turn, at most 5 total: a one-sentence situation + the decision +
    options where sensible. Stop asking once the remaining unknowns wouldn't change how the routine
    runs.

    EXCEPT the two mandatory answers from analyze_draft() — what the routine PRODUCES each run and
    what DONE looks like for one run. Whichever of those the draft left open is asked FIRST, and the
    stop-asking rule does not reach them: they always change how the routine runs, so no default is
    "obvious" and silence is not an answer."""


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
         "stages": {"<stage>.md": "<detail>", ...},                      # omit or {} unless it splits
         "notes": "<anything the creator should know>"}

    Everything above (refined_instruction, workflow_choice, params, …) goes INSIDE the `content`
    string — NEVER as top-level fields of the action. This is not a `finish`; the action has no
    `status`/`summary`/`workflow` fields. The refined_instruction must make sense to a fresh agent
    with no memory of this conversation; split into stages/<stage>.md files only for genuinely
    separable multi-step tasks.

    OWNERSHIP RULE — the instruction is the TASK, nothing else. Cross-cutting conduct is owned
    by TRAITS (practice modules adapted into the routine at creation: asking policy, LEDGER
    and web-research discipline, git checkpoints) and capabilities by user-set
    PERMISSIONS (messaging channels, util authoring, previous-run access): the
    refined_instruction must contain NONE of it, and must not assume any rule or permission is
    present. If the user's draft mixes conduct into the task ("message me on discord when...",
    "improve your own prompt after each run"), do not copy it into the instruction — flag it in
    `notes` as a rule/permission choice for the wizard. Conduct text baked into the instruction
    would keep acting after the user changes the routine's setup, which breaks their control
    surface."""


if __name__ == "__main__":
    main()
