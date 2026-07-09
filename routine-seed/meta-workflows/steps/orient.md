# Orient

Build the worklist of what is new since your last sweep.

## Do
1. `read_file ~/routines/<yourself>/state/last_seen.json` — a map `{routine: <last analyzed run ts>}`. Missing/empty means everything is unseen.
2. `ls ~/routines`. Keep only real routine directories: **skip any dot-directory and skip yourself** (the workflow-library maintainer).
3. For each remaining routine, `ls` its `runs/` and select run dirs whose timestamp is newer than `last_seen[routine]`. Cap at the **~5 newest unseen runs** per routine.
4. Record the selected transcript paths per routine into `state/phase.json.cursor.worklist` as `{routine: [<run dir>/transcript.jsonl, ...]}`.

## If nothing is new
If no routine has an unseen run: note "no new runs" for the LEDGER, then still perform one review pass — pick a single workflow (e.g. least recently touched) and carry it to the `draft-new-workflow`/`record` steps so the sweep is never a complete no-op. Set `cursor.worklist` empty and `cursor.no_new_runs = true`.

## Next
Write `state/phase.json = {step: "ingest-evidence", cursor: {worklist, no_new_runs}}`. Read `steps/ingest-evidence.md`.
