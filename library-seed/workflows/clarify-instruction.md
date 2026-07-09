---
name: Clarify instruction
slug: clarify-instruction
description: The wizard's workflow — interrogate a draft instruction until it is unambiguous, then emit a structured result for routine creation.
when_to_use: >
  Internal: drives the new-routine wizard. Takes a raw draft instruction as its instruction,
  asks the user blocking questions to resolve ambiguities, contradictions and scope, then
  writes state/wizard_result.json. Not for scheduled use.
version: 3
status: stable
params: []
default_budgets: {max_turns: 25, max_wall_clock_min: 30}
requires: {schema_output: false}
includes: [ask-policy]
---

## Run flow
1. **Analyze the draft** (it is your INSTRUCTION section). Hunt for: ambiguity (what exactly
   is the deliverable? for whom? where does it live?), contradictions, missing constraints
   (budget, language, sources, tone), outward acts (does this routine send/publish/spend —
   and what needs a per-item confirmation vs standing authorization?), and success criteria
   (when is it DONE done?).
   **Out of scope — never ask about, never include:** scheduling/frequency, the self-*
   standards (audit/improve/ledger/fresh-eyes/hygiene), the working directory, and model/
   endpoint choices. Those are routine CONFIGURATION, set separately in the UI. The
   instruction describes ONLY the task itself. If the draft mentions a schedule ("every
   Monday…"), treat it as a hint the user will configure and phrase the task per-run
   ("each run, cover what appeared since the last covered point, tracked in state/") —
   the task must make sense regardless of how often it fires.
2. **Resolve what you can yourself.** Obvious defaults don't deserve questions (ask-policy).
   List your assumptions explicitly for step 4.
3. **Ask the user** — `ask_user` with mode "blocking", ONE question per turn, at most 5
   total. Each question: one-sentence situation + the decision + options where sensible.
   Stop asking as soon as the remaining unknowns don't change what the routine would do.
4. **Synthesize the refined instruction.** Imperative, self-contained markdown: the goal,
   the concrete deliverable and where it lives, constraints, what is autonomous vs gated
   (outward acts), and completion criteria — schedule-free and phrased per-run. Fold in the
   user's answers and your stated assumptions. It must make sense to a fresh agent with no
   memory of this conversation. Keep this ENTRY instruction concise: it is the single entry
   point the routine always reads.
   **Split by function when the task has several substantial, separable steps** (like the
   grants/freelancing/birthday examples): keep the multi-sentence detail for each step in its
   own `playbook/<step>.md` file, and in the entry instruction just name the step and say
   "detailed instructions in playbook/<step>.md — read on demand". Don't split a simple task.
5. **Write the result** — `write_file` to `state/wizard_result.json`:
   `{"refined_instruction": "<the full ENTRY markdown>", "suggested_slug": "<kebab-case>",
     "suggested_name": "<short human name>",
     "playbook": {"<step>.md": "<detailed step instructions>", ...},  // omit or {} if not split
     "notes": "<anything the creator should know>"}`
6. **Finish ok**, summarizing the refined instruction in 3-6 lines.

## Phases
- **only** — one conversation, one run.

## Completion criteria
- state/wizard_result.json exists with a refined, schedule-free instruction that a fresh
  routine could execute without this conversation's context.
