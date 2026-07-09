---
name: General task
slug: general-task
description: The sane default — orient, do the current state's work in small verified steps, record, self-audit, improve, commit.
when_to_use: >
  Fits most recurring instructions that don't have a more specific pattern: collect/produce/
  maintain something on a schedule, tend a long-running goal, run a periodic check. If the
  instruction mostly says WHAT to deliver and the HOW is ordinary tool work, this is the one.
version: 6
status: stable
tags: [general]
params: []
default_budgets: {max_turns: 60, max_wall_clock_min: 45}
requires: {schema_output: false}
includes: [ask-policy, communication, global-utils, web-research, ledger-discipline, self-audit, improvement, fresh-eyes, hygiene]
modules: [bootstrap, steady, wrap-up]
---

## Run flow
You are a **state machine**. Read `state/phase.json` for your current state, then open and follow
that state's module with `read_file`. Every run, whatever the state:

1. **Orient.** Consume the state digest (phase, last result, LEDGER tail, user messages/answers)
   and check `LEDGER.md` before exploring anything new.
2. **Do the state's work.** Follow the current state's module (below), in small verified steps.
   You have NO shell — run code only through the `util` action; read files with read_file, write
   with write_file. Delegate separable chunks (research, bulk processing) to `spawn`ed
   sub-workflows; use `llm` subcalls for scoped one-shot judgments. Verify what you produce
   (read it back, check exit codes, count results) — never claim unverified outcomes.
3. **Record.** Update `state/phase.json` and any state files; append the LEDGER entry.
4. **Self-audit + improve.** Determine the routine's health, then act on it this same run.
5. **Close out.** `finish` with a 3–10 line summary: what was delivered, decisions, open ends.
   The engine commits your working directory automatically — you never run git.

## States
Track the current state in `state/phase.json` as `{"phase": "...", "note": "..."}`.

- **bootstrap** → `steps/bootstrap.md` — first run(s): set up and produce a first honest increment.
- **steady** → `steps/steady.md` — the normal cadence: advance the deliverable and improve.
- **wrap-up** → `steps/wrap-up.md` — the end state: finalize and propose disabling the schedule.

## Completion criteria
- Per run: the summary states a concrete increment, the LEDGER entry exists, everything committed.
- Overall: the instruction's deliverable exists, is current, and the user knows where it lives.
