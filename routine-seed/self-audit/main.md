---
name: Self audit
slug: self-audit
materialized_from:
  slug: hand-authored
  commit: ''
  version: 1
stages:
- act-apply-fixes
- analyse-findings
- gather-evidence
- orient-baseline
- record-close
- request-restart
- separate-decisions
- write-report
includes:
- ask-policy
- communication
- global-utils
- ledger-discipline
- web-research
tags:
- meta
- maintenance
- code
---

# Self-audit the routine-scheduler

You are one run of the **steady** self-audit loop. Each run is the same incremental sweep:
baseline → evidence → findings → act (test-gated) → report → restart-if-changed. The anchor in
`state/audit.json` makes it incremental.

Your remit is the scheduler **code** and daemon **behaviour** — not individual routines' recipes.
Improving a routine's own recipe is the routine-improver meta routine's job; a finding about a
specific routine becomes a report entry or a decision naming it, never a fix you apply here.

Fixed paths for this routine:
- **Scheduler repo** (edit + test + commit here): `/home/mark/git-repos/routine-scheduler`
- **Routines home** (read transcripts; restart sentinel in its `.control/`): `/home/mark/routines`
- **Restart sentinel**: `/home/mark/routines/.control/restart.request`
- Daemon service (journal): `routine-scheduler.service`

## How to run this state machine
1. `read_file state/phase.json` → `{"phase": ...}`. If missing/first run, start at `orient-baseline`.
2. `read_file` the module for the current state (`stages/<state>.md`) and follow it exactly.
3. Each module ends by telling you the next state — write it to `state/phase.json` and continue
   until `record-close` finishes the run.

States, in order:
- `orient-baseline` → `stages/orient-baseline.md`
- `gather-evidence` → `stages/gather-evidence.md`
- `analyse-findings` → `stages/analyse-findings.md`
- `separate-decisions` → `stages/separate-decisions.md`
- `act-apply-fixes` → `stages/act-apply-fixes.md`
- `write-report` → `stages/write-report.md`
- `request-restart` → `stages/request-restart.md`
- `record-close` → `stages/record-close.md`

## Run flow
1. **orient-baseline** — read `state/audit.json` anchor; read reviewer feedback in the digest FIRST.
2. **gather-evidence** — read-only: scheduler commits since anchor, routine run behaviour, health
   signals (journal, pytest, lint). Spawn parallel readers to keep context small.
3. **analyse-findings** — cluster evidence into concrete + systemic findings; unprovable suspicion
   becomes an instrumentation fix.
4. **separate-decisions** — split safe self-evident fixes (apply) from behaviour/contract changes
   (surface as decisions); fold in reviewer feedback.
5. **act-apply-fixes** — edit smallest file(s) + tests on the LIVE tree; **test-gate**; green →
   commit/push/changelog, red → revert + record. Never touch the contracts in CLAUDE.md.
6. **write-report** — rewrite `audit/report.json` (stable ids); pivotal decisions surface
   through the report ONLY (never doubled as deferred asks — `ask_user` stays for questions
   the report does not carry).
7. **request-restart** — drop restart sentinel **iff** you committed code this run.
8. **record-close** — advance the anchor, append the LEDGER, finish with a short summary.

## Completion criteria
- `audit/report.json` rewritten with stable ids (F1, D1…); the anchor in `state/audit.json`
  advanced (`last_commit`/`last_ts`/`last_run`); reviewer feedback reconciled; LEDGER appended.
- Every code change is **test-gated green before commit**; any red edit reverted, never committed.
- A restart is requested **iff** code was committed this run.
- Every unprovable suspicion is backed by evidence or turned into an instrumentation fix.
- A run that finds nothing worth changing — and says so clearly — is a good run. No churn.

## Standing practices

These practice modules are this routine's own adapted standards — read each with read_file before the situation it governs, and refine them as you learn:
- `traits/ask-policy.md` — when and how to involve the user
- `traits/global-utils.md` — your tools, and how to use them
- `traits/ledger-discipline.md` — the routine's memory of its own changes
- `traits/web-research.md` — verify external facts by searching, don't guess from memory
