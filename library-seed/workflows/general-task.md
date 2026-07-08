---
name: General task
slug: general-task
description: The sane default — orient, do the instruction's work in small verified steps, record, self-audit, improve, commit.
when_to_use: >
  Fits most recurring instructions that don't have a more specific pattern: collect/produce/
  maintain something on a schedule, tend a long-running goal, run a periodic check. If the
  instruction mostly says WHAT to deliver and the HOW is ordinary tool work, this is the one.
version: 1
status: stable
params: []
default_budgets: {max_turns: 60, max_wall_clock_min: 45}
requires: {schema_output: false}
includes: [ask-policy, ledger-discipline, self-audit, improvement, fresh-eyes, hygiene]
---

## Run flow
1. **Orient.** The state digest shows your phase, last result, LEDGER tail, and any user
   messages/answers — consume them first. Check LEDGER.md before exploring anything new.
   If this is the first run, do the bootstrap items in ## Phases.
2. **Pick the run's work.** From the instruction, the current phase, and anything the user
   sent: what does THIS run deliver? Prefer finishing in-progress work over starting new
   work. Guard standing obligations first (anything the instruction says must never slip).
3. **Execute in small verified steps.** Global utils (`gu <name> … --json`) are the primary
   tools; check `gu list` before hand-rolling anything. Verify what you produce (read the
   file back, check the exit code, count the results) — never claim unverified outcomes.
   Delegate separable chunks (research, bulk processing) to `subinstruction` sub-agents with
   self-contained prompts. Use `llm` subcalls for scoped one-shot judgments.
4. **Record.** Update `state/phase.json` and any state files; append the LEDGER entry
   (ledger-discipline). Keep artifacts the user reads coherent (fresh-eyes).
5. **Self-audit** (self-audit fragment) — determine only.
6. **Improve** (improvement fragment) — act on the audit, same run.
7. **Close out.** `git add -A && git commit -m "<run id>: <one line>"`. Then `finish` with a
   3-10 line summary: what was delivered, decisions taken, open ends. The summary is what
   the user and the next run see.

## Phases
Track the current phase in `state/phase.json` as `{"phase": "...", "note": "..."}`.
- **bootstrap** — first run(s): set up `state/`, understand the instruction's domain, file
  deferred questions for genuinely pivotal unknowns (ask-policy), produce a first honest
  increment of the deliverable. Advance to steady when the basic loop produced real output
  once.
- **steady** — the normal cadence: each run advances the deliverable, tends feedback, and
  improves the process. Stay here while the instruction describes ongoing work.
- **wrap-up** — when the instruction's end state is reached (or the user says stop):
  finalize the deliverable, write a closing LEDGER entry, and file a deferred question
  proposing to disable the schedule.

## Completion criteria
- Per run: the summary states a concrete increment, the LEDGER entry exists, and everything
  is committed.
- Overall: the instruction's deliverable exists, is current, and the user has been told
  where it lives.
