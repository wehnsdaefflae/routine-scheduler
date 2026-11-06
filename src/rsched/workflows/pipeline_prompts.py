"""The materialization pipeline's PROMPTS — every sentence the generator reads while it turns a
workflow pattern plus an instruction into a recipe (`pipeline.py` runs them).

These are where the recipe rules are enforced at their only enforceable point: a recipe is
LLM-written, so the generator is the cause. WHAT, never which tool (a name-matching linter over a
finished file would go red the day a util is named after an ordinary word); instructions, never
history; and a contract that names only the task (`taskcontract.RECIPE_CONTRACT`) — the one rule
of the four that ALSO has a machine check behind it, because its defects have a fixed vocabulary.
"""

from __future__ import annotations

from .taskcontract import RECIPE_CONTRACT

CONTEXT = """\
You are generating a ROUTINE by applying a workflow pattern to a specific task.

WORKFLOW (a Python control-flow pattern — a precise depiction you do NOT execute):
---
{workflow}
---

INSTRUCTION (the task this routine runs):
---
{instruction}
---

"""

SELF_CONTAINED = """

SELF-CONTAINED — the running agent acts ONLY from main.md and the stage modules; the INSTRUCTION
above will NOT exist at run time. So INLINE every concrete detail the task needs directly into
them: exact values, thresholds, names, formats, category lists, file paths, URLs, output shapes,
completion criteria. Never write "as the instruction says" or otherwise defer to the instruction —
it is the SEED you are compiling from, not a document the run can read.

WHAT, NEVER WHICH TOOL — a recipe describes the WORK, never the toolbox. Never name a util, and
never write a util's flags or invocation. Name the CAPABILITY the step needs ("fetch the page",
"run the repo's test suite", "send the mail", "publish the site") and leave the choice of tool to
the run: it is shown the live tool catalog in its CAPABILITIES section, and it records what worked
in its own memory across runs. A tool named here is stale the day it is renamed or removed, and it
stops the run from discovering a better one. This does NOT soften the inlining rule above: TASK
facts (values, thresholds, paths, formats) belong in the recipe; TOOL choices never do.

INSTRUCTIONS, NEVER HISTORY — write only what the run must DO. The agent reading this recipe has
never seen an earlier draft of it, so a sentence contrasting the current design with a previous
one is invisible to it: it costs tokens every turn and teaches nothing, and "X was retired" or
"the old approach did Y" read as facts about the world it should act on. Forbidden in every line
you write: "no longer", "used to", "previously", "as of the last change", "was retired/replaced/
renamed", "nothing replaced it", "never reintroduce", "don't revert to", "the old/former/previous
<thing>", and dates or counts that were true once. State the current design in the present tense
and stop. Where a rule exists for a reason that matters, express the reason as a consequence of
following it now ("commit before the multi-file edit so you have an undo point"), never as the
history of the decision.

A CONTRACT NAMES ONLY THE TASK — """ + RECIPE_CONTRACT

OUTLINE_TAIL = """\
Plan this routine's STAGE OUTLINE — the set of stage modules a fresh agent will work through in
order, one per step/state of the workflow, tailored to THIS task. Each stage will be generated
as its own module from this outline.

Return JSON {"stages": [{"name", "scope", "inputs", "outputs"}, ...]} with 3-8 entries:
- name: kebab-case, concrete and specific to THIS task — the filenames are the live progress
  diagram the user watches, so each must read as this task's real step (never a generic
  workflow or function name).
- scope: what this stage ALONE covers. Scopes must be MUTUALLY EXCLUSIVE and together cover the
  workflow's whole control flow — no step of the pattern may be missing, none owned twice.
- inputs: what the stage reads (state/ files, prior stages' outputs, external sources).
- outputs: the exact files/decisions it produces (state/ paths, deliverables).
Every stage does TASK work. No stage whose scope is recording the run (a ledger entry, a work
log, a phase or notes write) or orienting on that record — the engine keeps the run's record
and hands the next run its digest, so such a stage is turns spent on nothing the task needs."""

MAIN_RULES = """\
Write "main": the routine's ENTRY (main.md body). A state machine — it tells the run to read +
follow the current stage's module with read_file, working through the stages in order (the
engine derives the run's live position from those reads: reading `stages/<name>.md` marks the
run as IN that stage — no progress bookkeeping is needed). Durable state a FUTURE run needs (a
lifecycle marker, a cursor) lives in its own `state/` file the stages define. It keeps a
`## Run flow` section and a `## Done when` section. `## Run flow` is a NUMBERED list;
every item LEADS with a **bold** stage name matching the stage filename and names its file as
`stages/<name>.md` — reference EVERY stage of the outline, in its order. Turn the pattern's
control flow into concrete prose for THIS task — never leave Python in the output.

`## Done when` lists what ONE finished run leaves behind — one line per outcome, exactly
`- d1 · <stage-name> — <outcome>`, where `<stage-name>` is the outline stage that produces it,
or `- d1 — <outcome>` when the outcome spans the whole run; ids d1, d2, … in order. Take the
pattern's DONE_WHEN as the skeleton and THIS task's words as the content. Each line is an
OUTCOME of the TASK that the run's own transcript can show — never of the run's own record (see
A CONTRACT NAMES ONLY THE TASK below); never "at least N"; never a cap
phrased as a quota ("at most one open question and only a real one", not "one open
question"); never a prohibition — what a run must never do belongs to its permissions and
rules, not here; and every line can honestly be answered "not due, because <how that was
established>" on a run where it does not apply. At its finish the run accounts for every
line; a `met` is checked against its transcript — so write what can be checked.
Whether the ROUTINE ever finishes for good is not the recipe's business: that is the
operator's finish line, kept outside the recipe. A `## Never` section is written only when the
user named prohibitions (they are listed below when they did): one line each, in their words.

PACE. Say how much one run attempts, and let it be as much as it can finish well: the turn
budget is a runaway BACKSTOP, never a ration, and a run that leaves work it could have finished
has paced itself against a counter instead of the job. Never write a per-run ARITY — "exactly
one increment", "one high-value thing", "a few", "at most N", "do not try to finish it all" —
UNLESS the work is genuinely serialized by something outside the run, and then name that thing
(one submission per round, one training job on a shared GPU, one reply per partner per week).
An external constraint stated is a boundary; an arity asserted is a brake. Equally: say what
makes a run EXIT EARLY — when nothing is due, the run establishes that, says so, and finishes.

Return ONLY the JSON object {"main": ...}."""

STAGE_RULES = """\
- Cover exactly this stage's scope — the concrete procedure (what to read, decide, do, write
  and verify), the exact `state/` files and output shapes it touches, its edge cases, and what
  done looks like; typically 20-60 lines. A one-line summary or placeholder stub is a FAILURE —
  at run time the agent has NOTHING else to act from.
- Do NOT restate other stages' procedures — each scope is owned by its own module; end by
  routing the way main.md does (the next stage, or the run's close).
- Turn the pattern's step into concrete prose for THIS task — never leave Python in the output.

Return ONLY the JSON object {"body": ...}."""
