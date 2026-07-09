---
name: Meta — self-audit the scheduler
slug: self-audit-code
description: Audits routine-scheduler's OWN code and behaviour since the last audit (git history, every routine's transcripts/LEDGERs, daemon logs, tests/lint), writes the report the Audit tab renders, then applies safe fixes + settled decisions to the live tree — test-gated, committed, and followed by a graceful server restart.
when_to_use: >
  Internal: the standing self-audit routine that inspects and improves the routine-scheduler
  codebase itself. Not a template for user routines. Requires fs_read_roots over the scheduler
  repo + the routines home, and fs_write_roots over the scheduler repo + the routines-home
  `.control/` dir (to drop the restart sentinel). Orchestrator should be a strong model
  (Opus via the claude-cli endpoint). Paths (scheduler repo, routines home) come from the
  instruction.
version: 3
status: stable
tags: [meta, maintenance, code]
params: []
default_budgets: {max_turns: 80, max_wall_clock_min: 60}
requires: {schema_output: false}
includes: [ask-policy, communication, global-utils, ledger-discipline, web-research]
---

## Run flow
1. **Orient + baseline.** Read `state/audit.json` (`{last_commit, last_ts, last_run}`) — the
   anchor for "since the last audit". First run: no anchor, so audit broadly and set it at the
   end. Read the reviewer feedback waiting in the state digest FIRST (see **Reviewer feedback**) —
   it steers what you act on this run.
2. **Gather evidence** (read-only):
   - **Scheduler changes** since `last_commit`: use a util (`util name=list`; `write_util` a
     git-log util if none exists — `git -C <repo> log <ref>..HEAD --stat`).
   - **Routine behaviour**: for each routine under the routines home (skip dot-dirs and
     yourself), read the top-level `transcript.jsonl` + `LEDGER.md` of runs newer than
     `last_ts`. Cap the newest ~5 each; `spawn` parallel readers (prompt = paths + the rubric)
     so your context stays small, then `wait` all. Collect finish outcomes (authored vs
     budget-forced), schema-retry storms, repeated-action warnings, fabrication-guard
     rejections, wasted turns, questions asked (answered vs ignored), workflow-vs-run conflicts.
   - **Health signals**: daemon errors/overruns/orphans (a util over the journal/log file); the
     current test + lint status (`util pytest-run <repo>`; `util` running lint).
3. **Analyse into findings.** Cluster into concrete items — problems, improvements, redundancies,
   and *systemic* issues (a class of failure across routines). Note the evidence for each. **If a
   suspicion is unprovable for lack of data, make it a finding whose fix is the specific
   logging/telemetry to add** so the next audit can see it (that fix lands in step 5).
4. **Separate what you can settle from what needs the user.** A safe, self-evident fix stays a
   finding you will apply. A choice that changes behaviour/priorities or is irreversible becomes a
   **decision** (2–4 options, always incl. "leave as-is"). Fold in reviewer feedback: a comment
   tunes or closes its finding; a chosen decision is a settled work order; a note is guidance.
5. **Act — apply the safe fixes + settled decisions to the LIVE scheduler tree.** You edit the
   real tree directly (the decision was: no worktree), so precision and the test gate are what
   keep the daemon safe:
   - Edit the smallest responsible file(s) with `write_file`; keep diffs small and reviewable.
     **Add/adjust tests in the same change** (the repo requires tests with every change). For an
     unprovable suspicion, add the logging you specified. Do NOT touch the scheduler's core
     contracts (the action schema, transcript `EVENT_TYPES`, the ownership rules in CLAUDE.md) as
     a "self-evident fix" — those are DECISIONS; surface them, don't apply them.
   - **Test-gate — the hard gate.** Run `util pytest-run <repo>` (from the instruction).
     - **GREEN** → commit + push: `util git-sync <repo> -m "self-audit: <one line>"`. Read the
       new commit hash back (git-log util) and append one line to `audit/changelog.jsonl`:
       `{"ts": "<iso>", "commit": "<hash>", "summary": "<what changed & why>", "run_id": "<this run>"}`.
     - **RED** → revert with `util git-restore <repo> <the files you touched>` so the tree is
       clean again, record the failed attempt as a finding (what broke, from the test tail), and
       do NOT commit. **Never promote a red edit.** Try at most a small fix-and-retest; if still
       red, leave it reverted and surface it.
   - Respect the improvement fragment's autonomy gate: reversible code/tests/config = act;
     goal/contract changes or anything outward = a decision.
6. **Write the audit report** the Audit tab reads — `audit/report.json`, exactly this shape:
   ```json
   {"schema": 1, "run_id": "<this run id>", "generated": "<iso8601>",
    "since": {"commit": "<short hash or ''>", "window": "<e.g. '4 runs, 3 routines, 2 days'>"},
    "summary": "<1–3 sentence health readout, incl. what you changed this run>",
    "findings": [{"id": "F1", "severity": "problem|improvement|redundancy|systemic|info",
                  "title": "<short>", "detail": "<what & why; note if fixed this run>",
                  "evidence": ["<run-id / path / log ref>", "..."]}],
    "decisions": [{"id": "D1", "title": "<short>", "detail": "<context>",
                   "options": ["<A>", "<B>", "leave as-is"]}]}
   ```
   Stable ids (F1, D1…) so reviewer comments attach and carry across runs. For pivotal decisions
   also file a deferred `ask_user` (respect the ask cap) so they hit the Questions inbox too.
7. **Request a restart — only if you committed code this run.** The daemon is still running the
   OLD code until it relaunches. Drop the restart sentinel with `write_file` to
   `<routines_home>/.control/restart.request` (routines_home from your instruction; your config
   grants that dir), content e.g. `{"reason": "self-audit <one line>", "requested": "<iso>"}`.
   The daemon then drains (fires no new runs, waits for every run — including this one — to
   finish) and restarts on your new code; it will NOT restart while any run is parked waiting on
   the user, and never kills a run. You do nothing further — just finish.
8. **Record + close out.** Update `state/audit.json` (advance `last_commit`/`last_ts`/`last_run`).
   Append the LEDGER entry (findings, edits committed, reverts, decisions surfaced, reviewer
   feedback reconciled). The engine commits your own directory automatically. Finish with a short
   summary: headline health, findings by severity, commits made, decisions awaiting the user,
   whether a restart was requested.

### Reviewer feedback (from the Audit tab, via your inbox)
The Audit tab writes the reviewer's input as ordinary inbox messages you receive in the state
digest, tagged so you can route them:
- `[AUDIT feedback · finding F1] <text>` — a comment on F1: tune it, close it, or fold the
  correction in; record how you resolved it.
- `[AUDIT decision · D1] selected: <option> (— <note>)` — the ruling on D1: settled work order.
- `[AUDIT note] <text>` — free guidance not tied to an item: weigh it this run.
Everything the reviewer submits must be considered on the run after they submit it.

## Phases
- **steady** — every run is the same sweep: baseline → evidence → findings → act (test-gated) →
  report → restart-if-changed. The anchor in `state/audit.json` makes each run incremental.

## Completion criteria
- Per run: `audit/report.json` rewritten (stable ids), the anchor advanced, reviewer feedback
  reconciled, and the LEDGER appended.
- Every code change is **test-gated green before commit**; a red edit is reverted and never
  committed. A restart is requested **iff** code was committed this run.
- Every unprovable suspicion is either backed by evidence or turned into an instrumentation fix.
