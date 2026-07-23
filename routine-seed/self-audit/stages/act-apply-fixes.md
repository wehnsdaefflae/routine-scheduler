# Step: act-apply-fixes — the test-gated heart of the run

Apply the APPLY list to the LIVE scheduler tree (`/home/mark/git-repos/routine-scheduler`).
There is no worktree — precision and the test gate keep the daemon safe.

APPLY may only contain what the autonomy gate authorized: items inside your lenses that pass
the safety condition, plus decisions the user settled.
If APPLY is empty, skip straight to Next (a no-change run is a good run — say so in the report).

## For each change
1. **Locate via the codemap, then read narrowly**: `modules-*.md` names the owning module,
   symbol and line; `routes.md` maps an endpoint to its handler and its static/ callers;
   `frontend.md` maps a JS module's exports/imports. Fetch with `util sym read <file> <Symbol>`
   (keep the returned hash) or a `read_file` window; fall back to grep only where the map has
   no answer.
2. Edit the **smallest responsible file(s)**; keep diffs small and reviewable, and respect the
   repo's one-responsibility / ≤~350-line rule. Pick the tool by scale: a function/class
   rewrite is `util sym replace <file> <Symbol> --hash <from THIS run's read> --body …` —
   compare-and-swap plus a whole-file re-parse, so a stale read or a syntax break is rejected
   loudly and nothing lands; line-scale tweaks and prose are `edit_file`; new files are
   `write_file`.
3. **Add or adjust tests in the SAME change** — the repo requires tests with every change. For an
   instrumentation finding, add exactly the logging/telemetry you specified (+ its test).
4. **Do NOT touch the contracts** (action schema, transcript `EVENT_TYPES`, CLAUDE.md ownership
   rules) here — those are decisions; they should already be in SURFACE, not APPLY.

## Test-gate — the hard gate
**Pre-gate first**:
1. Self-review each edited file: `util sym diff <file>` (no `--since` = HEAD → working tree)
   shows exactly what you changed, scoped per symbol — catch over-replacement and stray edits
   while they cost one observation, not a test cycle.
2. `util sym check <every .py/.js/.json file you edited>` — a syntax break caught here costs
   seconds; the same break at the pytest gate costs a full test cycle plus a revert.
Then run `util pytest-run /home/mark/git-repos/routine-scheduler`.
- **GREEN** →
  1. `util git-sync /home/mark/git-repos/routine-scheduler -m "self-audit: <one line>"` (commit+push).
  2. Read the new commit hash back (the git-log util from gather-evidence; if none exists,
     `write_util` a small one).
  3. Append one line to `audit/changelog.jsonl`:
     `{"ts":"<iso>","commit":"<hash>","summary":"<what changed & why>","run_id":"<this run>"}`.
  4. Set a flag `committed_code=true` (drives the restart request).
- **RED** →
  - `util git-restore /home/mark/git-repos/routine-scheduler <the files you touched>` so the
    tree is clean again.
  - Record the failed attempt as a finding (what broke, from the test tail). Do NOT commit.
  - You may try **at most one** small fix-and-retest. If still red, leave it reverted and surface
    it. **Never promote a red edit.**

Batch related edits into one commit where sensible; keep unrelated changes as separate commits.

## Next
Write `state/phase.json` = `{"phase": "write-report"}` and read `stages/write-report.md`.
