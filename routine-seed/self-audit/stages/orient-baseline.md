# Step: orient-baseline

Establish the "since last audit" anchor and let the reviewer steer this run.

## Do
1. `read_file state/audit.json` → `{last_commit, last_ts, last_run}`. This is the anchor for
   "since the last audit".
   - **First run** (missing/empty): there is no anchor. Plan to audit broadly and set the anchor
     at the end (`record-close`). Note in your working memory that this is a first, broad run.
2. **Read the reviewer feedback FIRST** — scan the state digest / inbox for the tagged messages
   and route each one; they steer what you act on this run:
   - `[AUDIT feedback · finding F1] <text>` — a comment on an existing finding: plan to tune it,
     close it, or fold the correction in.
   - `[AUDIT decision · D1] selected: <option> (— <note>)` — a settled work order to execute.
   - `[AUDIT note] <text>` — free guidance to weigh this run.
   Everything the reviewer submitted must be considered on this run. Hold a short list of
   feedback items to reconcile in `analyse-findings` / `act-apply-fixes` and record in the report.
3. Load last run's `audit/report.json` (if present) so finding/decision ids (F1, D1…) stay stable
   and reviewer comments attach to the right item.
4. **Regenerate + load the codemap** — the pre-built lookup surface that replaces most code
   exploration: `util codemap args=["--repo", "/home/mark/git-repos/routine-scheduler"]`
   (seconds, deterministic, writes `<repo>/.codemap/`), then
   `read_file /home/mark/git-repos/routine-scheduler/.codemap/index.md`.
   **Standing rule for the whole run: look up before you read.** Resolve "which file owns X /
   what's the API surface / who calls what" from `.codemap/` (`modules-*.md` for Python
   symbols+signatures, `routes.md` for endpoints + their static/ callers, `frontend.md` for
   JS modules, `contracts.md` for action kinds / event types / config fields) and open a
   source file only for the exact lines the map names. The map is derived state — regenerate
   it, never edit it; a mushy map entry means a mushy docstring, which is itself a small fix.

## Next
Write `state/phase.json` = `{"phase": "gather-evidence"}` and read `stages/gather-evidence.md`.
