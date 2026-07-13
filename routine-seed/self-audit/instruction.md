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
  report: findings with concrete evidence, and decisions where you need my call. This reporting
  duty is unconditional — every run produces it.
- **Act on findings, in these lenses**: defect fixes (plus the logging/telemetry a thin
  suspicion needs); waste reduction; small self-contained affordances; interface/artifact
  quality — each grounded in current best practice (research before you patch). All acting is
  **test-gated** — commit + push, log to the changelog, request the restart. Changing the
  action-schema / transcript-event / ownership contracts, or anything scope-shaped, is a
  decision for me, not a fix.
- Act on my feedback from the Audit tab: comments on findings, decisions I settle, general
  notes. A decision I settled is explicit authorization — apply it (test-gated).
- Your remit is the scheduler CODE and daemon behaviour. Improving individual routines'
  recipes is the routine-improver meta routine's job, not yours — findings about a specific
  routine become report entries or decisions naming it.

## Paths & conventions
- Scheduler repo (edit + test + commit here): `/home/mark/git-repos/routine-scheduler`
- Routines home (read transcripts; the restart sentinel lives in its `.control/` dir):
  `/home/mark/routines`
- The repo's own conventions bind you too: one responsibility per file (≤ ~350 lines), tests
  accompany every change, and the action-schema / transcript-event / ownership contracts in its
  CLAUDE.md must NOT be changed as "self-evident fixes" — those are decisions for me.
- The goal is to raise code quality and close the loop on my feedback — not churn. A run that
  finds nothing worth changing, and says so clearly, is a good run.
