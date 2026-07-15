# Orient

Build the worklist of what is new since your last sweep.

## Do
1. **Start from the usage evidence**: `read_file ~/routines/.control/workflow-usage.jsonl` — one
   line per finished run AND per finished sub-workflow (`{routine, run_id, workflow, depth, status,
   turns, tokens}`). It tells you which patterns are actually used (including the per-purpose child
   patterns parents spawn, `depth > 0`), which fail or burn outsized turn/token budgets, and which
   are never picked — dead weight to question, or a sign the spawn catalog lacks a fitting pattern.
   Weight your attention by this evidence before opening any transcript.
2. `read_file ~/routines/<yourself>/state/last_seen.json` — a map `{routine: <last analyzed run ts>}`. Missing/empty means everything is unseen.
3. List `~/routines` with a directory-listing util (`util name=list` to find one; if none exists, `write_util` a tiny one). Keep only real routine directories: **skip any dot-directory and skip yourself** (the workflow-library maintainer).
4. For each remaining routine, list its `runs/` the same way and select run dirs whose timestamp is newer than `last_seen[routine]`. Cap at the **~5 newest unseen runs** per routine.
5. Record the selected transcript paths per routine into `state/phase.json.cursor.worklist` as `{routine: [<run dir>/transcript.jsonl, ...]}`.

## If nothing is new
If no routine has an unseen run: note "no new runs" for the LEDGER, then still perform one review pass — pick a single workflow (e.g. least recently touched) and carry it to the `draft-new-workflow`/`record` steps so the sweep is never a complete no-op. Set `cursor.worklist` empty and `cursor.no_new_runs = true`.

## Next
Write `state/phase.json = {step: "ingest-evidence", cursor: {worklist, no_new_runs}}`. Read `stages/ingest-evidence.md`.
