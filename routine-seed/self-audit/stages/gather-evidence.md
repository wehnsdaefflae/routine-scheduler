# Step: gather-evidence (read-only)

Collect the raw signal. Touch nothing; keep your own context small by spawning parallel readers.
Lookups go through the codemap first (orient step 4); when spawning a reader, name the relevant
`.codemap/` file (or paste the few lines that matter) in its brief so the child looks up too
instead of re-exploring the tree.

## A. Scheduler code changes since the anchor
- Use a util to git-log the repo since `last_commit` (`util name=list` to find one). If no such
  util exists, `write_util` a small one: `git -C /home/mark/git-repos/routine-scheduler log
  <last_commit>..HEAD --stat` (first run: log the recent history broadly, e.g. last ~20 commits).
- Capture: which files changed, churn hot-spots, any file now over the ~350-line one-responsibility
  budget, changes that touch contracts (action schema, transcript `EVENT_TYPES`, CLAUDE.md).

## B. Routine runtime behaviour (`/home/mark/routines`)
- Enumerate the routines home with a directory-listing util (`util name=list`; if none exists,
  `write_util` a tiny one). **Skip dot-dirs and this routine itself.**
- For runs newer than `last_ts`, read the top-level `transcript.jsonl` + `LEDGER.md` (+ peek
  `status.json`). **Cap the newest ~5 runs each.**
- **`spawn` parallel readers** (prompt = the file paths + the rubric below), then `wait` all.
- Rubric — collect per run: finish outcome (authored vs budget-forced), schema-retry storms,
  repeated-action warnings, fabrication-guard rejections, wasted turns, questions asked
  (answered vs ignored), and any workflow-vs-run conflicts.

## C. Health signals
- Daemon journal: `util service-logs` with args like
  `["--since", "<last_ts as YYYY-MM-DD HH:MM:SS>", "--grep", "error|overrun|orphan", "--json"]`
  (first run: omit `--since`, the default window is 24h). Record errors / overruns /
  orphaned runs; a journal-unavailable error is itself a finding, not a stop.
- Tests: `util pytest-run /home/mark/git-repos/routine-scheduler` — record pass/fail + tail.
- Lint: if a lint util exists (`util name=list`), run it and record status; otherwise skip —
  the pytest gate is the hard gate.

## D. UI friction
- Read the newest 1–2 files under `/home/mark/routines/.ui-traces/` (`<YYYYMMDD>.jsonl`,
  one event per line: ts/kind/view/target/detail). No dir or no files = the console wasn't
  used — skip silently, that is not a finding.
- Collect: `error` events (broken flows — pair each with its view), repeated `click` on one
  target within a minute (friction/rage-clicks), `reconnect` bursts (stream instability).
  These feed the interface-quality lens in analyse-findings.

## E. Mechanical inventory — free from the codemap
- The codemap regenerated at orient (`/home/mark/git-repos/routine-scheduler/.codemap/index.md`)
  pre-computes the cruft inventory: files over the ~350-line budget, orphan-module candidates,
  modules no test imports, skipped tests, TODO/FIXME lines, MIGRATION markers with expiry,
  stale path references in docs, churn hotspots. Treat each flag as a CANDIDATE: verify
  (grep / zero-reference check) before it becomes a finding — precision over recall.

## Next
Write `state/phase.json` = `{"phase": "analyse-findings"}` and read `stages/analyse-findings.md`.
