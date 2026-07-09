---
name: General task
slug: general-task
description: The sane default — orient, do the instruction's work in small verified steps, record, self-audit, improve, commit.
when_to_use: >
  Fits most recurring instructions that don't have a more specific pattern: collect/produce/
  maintain something on a schedule, tend a long-running goal, run a periodic check. If the
  instruction mostly says WHAT to deliver and the HOW is ordinary tool work, this is the one.
version: 3
status: stable
params: []
default_budgets: {max_turns: 60, max_wall_clock_min: 45}
requires: {schema_output: false}
includes: [ask-policy, global-utils, ledger-discipline, self-audit, improvement, fresh-eyes, hygiene]
---

## Run flow
1. **Orient.** The state digest shows your phase, last result, LEDGER tail, and any user
   messages/answers — consume them first. Check LEDGER.md before exploring anything new.
   If this is the first run, do the bootstrap items in ## Phases.
2. **Pick the run's work.** From the instruction, the current phase, and anything the user
   sent: what does THIS run deliver? Prefer finishing in-progress work over starting new
   work. Guard standing obligations first (anything the instruction says must never slip).
3. **Execute in small verified steps.** You have NO shell — run code only through the `util`
   action (the GLOBAL UTILS section lists what exists). If nothing fits, `write_util` to
   create one (a selftested PEP 723 script; it may call sibling utils), then call it. Read
   files with read_file, write them with write_file. Verify what you produce (read it back,
   check the util's exit code, count results) — never claim unverified outcomes. Delegate
   separable chunks (research, bulk processing) to parallel sub-workflows: `spawn` them (pick
   a fitting library workflow; give each a self-contained prompt and disjoint outputs), keep
   working, monitor with `subruns`, collect via the finish notifications or `wait`. Use `llm`
   subcalls for scoped one-shot judgments.
4. **Record.** Update `state/phase.json` and any state files; append the LEDGER entry
   (ledger-discipline). Keep artifacts the user reads coherent (fresh-eyes).
5. **Self-audit** (self-audit fragment) — determine only.
6. **Improve** (improvement fragment) — act on the audit, same run.
7. **Close out.** `finish` with a 3-10 line summary: what was delivered, decisions taken,
   open ends. The engine commits your working directory automatically — you never run git.
   The summary is what the user and the next run see.

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
