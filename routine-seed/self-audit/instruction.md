# Self-audit the routine-scheduler

Audit the routine-scheduler system — its own source code and its runtime behaviour — and keep it
healthy and improving, run over run.

## What to audit
- **The code**: `/home/mark/git-repos/routine-scheduler` (the scheduler repo the daemon runs on).
  Read its source, tests, and git history since your last audit.
- **The behaviour**: `/home/mark/routines` — every routine's runs (`transcript.jsonl`,
  `LEDGER.md`, `status.json`) since your last audit — and the daemon's own logs (the systemd user
  journal for `routine-scheduler.service`).

## What to produce each run
- Surface problems, improvement openings, redundancies, and systemic issues in the Audit tab's
  report: findings with concrete evidence, and decisions where you need my call.
- Apply the safe, self-evident fixes yourself — **test-gated** — to the repo, commit + push, log
  them to the changelog, and request the restart so the daemon picks up the new code.
- When evidence is too thin to judge a suspicion, add the logging/telemetry that would let the
  next audit see it (that is itself a code change).
- Act on my feedback from the Audit tab: comments on findings, decisions I settle, general notes.

## Paths & conventions
- Scheduler repo (edit + test + commit here): `/home/mark/git-repos/routine-scheduler`
- Routines home (read transcripts; the restart sentinel lives in its `.control/` dir):
  `/home/mark/routines`
- The repo's own conventions bind you too: one responsibility per file (≤ ~350 lines), tests
  accompany every change, and the action-schema / transcript-event / ownership contracts in its
  CLAUDE.md must NOT be changed as "self-evident fixes" — those are decisions for me.
- The goal is to raise code quality and close the loop on my feedback — not churn. A run that
  finds nothing worth changing, and says so clearly, is a good run.
