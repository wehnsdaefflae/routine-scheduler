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
                "options": ["<A>", "<B>", "leave as-is"]}]}
```

## Do
1. Write every finding from `analyse-findings` (stable ids), marking those **fixed this run** in
   `detail`, and every open decision from the SURFACE list.
2. `since.commit` = the anchor's short hash (`''` on first run); `since.window` = the runs /
   routines / days you swept.
3. `summary` = headline health + what you changed this run (commits) + decisions awaiting the user.
4. Reflect reconciled reviewer feedback (tuned/closed findings; executed decisions).
5. For each **pivotal** decision, also file a deferred `ask_user` so it hits the Questions inbox —
   **respect the ask cap** (only the most consequential; don't spam).

## Next
Write `state/phase.json` = `{"state": "request-restart"}` and read `steps/request-restart.md`.
