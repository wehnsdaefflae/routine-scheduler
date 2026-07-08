---
name: Clarify instruction
slug: clarify-instruction
description: The wizard's workflow — interrogate a draft instruction until it is unambiguous, then emit a structured result for routine creation.
when_to_use: >
  Internal: drives the new-routine wizard. Takes a raw draft instruction as its instruction,
  asks the user blocking questions to resolve ambiguities, contradictions, scope and cadence,
  then writes state/wizard_result.json. Not for scheduled use.
version: 1
status: stable
params: []
default_budgets: {max_turns: 25, max_wall_clock_min: 30}
requires: {schema_output: false}
includes: [ask-policy]
---

## Run flow
1. **Analyze the draft** (it is your INSTRUCTION section). Hunt for: ambiguity (what exactly
   is the deliverable? for whom? where does it live?), contradictions, missing cadence (how
   often, and why that rhythm?), unstated constraints (budget, language, sources, tone),
   outward acts (does this routine send/publish/spend — and what needs a per-item
   confirmation vs standing authorization?), and success criteria (when is it DONE done?).
2. **Resolve what you can yourself.** Obvious defaults don't deserve questions (ask-policy).
   List your assumptions explicitly for step 4.
3. **Ask the user** — `ask_user` with mode "blocking", ONE question per turn, at most 5
   total. Each question: one-sentence situation + the decision + options where sensible.
   Stop asking as soon as the remaining unknowns don't change what the routine would do.
4. **Synthesize the refined instruction.** Imperative, self-contained markdown: the goal,
   the concrete deliverable and where it lives, cadence + rationale, constraints, what is
   autonomous vs gated (outward acts), and completion criteria. Fold in the user's answers
   and your stated assumptions. It must make sense to a fresh agent with no memory of this
   conversation.
5. **Write the result** — `write_file` to `state/wizard_result.json`:
   `{"refined_instruction": "<the full markdown>", "suggested_slug": "<kebab-case>",
     "suggested_name": "<short human name>", "suggested_cron": "<cron or empty>",
     "suggested_tz": "Europe/Berlin", "notes": "<anything the creator should know>"}`
6. **Finish ok**, summarizing the refined instruction in 3-6 lines.

## Phases
- **only** — one conversation, one run.

## Completion criteria
- state/wizard_result.json exists with a refined instruction that a fresh routine could
  execute without this conversation's context.
