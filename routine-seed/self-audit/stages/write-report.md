# Step: write-report

Rewrite the report the Audit tab reads: `audit/report.json`. Exactly this shape:

```json
{"schema": 1, "run_id": "<this run id>", "generated": "<iso8601>",
 "since": {"commit": "<short hash or ''>", "window": "<e.g. '4 runs, 3 routines, 2 days'>"},
 "summary": "<1-3 sentence health readout, incl. what you changed this run>",
 "findings": [{"id": "F1", "severity": "problem|improvement|redundancy|systemic|info",
               "title": "<short>", "detail": "<what & why; note if fixed this run>",
               "evidence": ["<run-id / path / log ref>", "..."]}],
 "decisions": [{"id": "D1", "title": "<short>", "detail": "<context>",
                "options": ["<A>", "<B>", "leave as-is"],
                "status": "open"}]}   # "open" | "settled" — settled stays for the record
```

## Do
1. Write every finding from `analyse-findings` (stable ids), marking those **fixed this run** in
   `detail`, and every open decision from the SURFACE list.
2. `since.commit` = the anchor's short hash (`''` on first run); `since.window` = the runs /
   routines / days you swept.
3. `summary` = headline health + what you changed this run (commits) + decisions awaiting the user.
4. Reflect reconciled reviewer feedback (tuned/closed findings; executed decisions — set a
   decision's `status` to `"settled"` once acted on; keep it in the report for the record).
5. Decisions live in the report ONLY — the Decisions page surfaces every `"status": "open"`
   decision automatically (meta badge). NEVER also file them as deferred `ask_user`: that
   would double-surface them. `ask_user` remains for questions that are not report
   decisions (mid-run clarifications).
6. **Rewrite `audit/report-index.md` beside the report** — one line per item, nothing more:
   `F1 | problem | open|fixed | <title>` / `D1 | open|settled | <title>`. It is the cheap
   id-stability surface the next run's orient reads instead of the full report.

## Next
Write `state/phase.json` = `{"phase": "request-restart"}` and read `stages/request-restart.md`.
