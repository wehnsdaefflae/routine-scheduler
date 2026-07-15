# Step: request-restart — only if you committed code this run

The daemon runs the OLD code until it relaunches. Request a restart **iff** `committed_code` is
true (i.e. you committed at least one change in `act-apply-fixes`).

## If you committed code
`write_file` to `/home/mark/routines/.control/restart.request` with content e.g.:
```json
{"reason": "self-audit <one line>", "requested": "<iso>"}
```
The daemon then drains (fires no new runs, waits for every run — including this one — to finish)
and restarts on your new code. It will NOT restart while a run is parked on the user, and never
kills a run. Do nothing further about the restart.

## If you committed nothing
Do not write the sentinel. There is nothing new for the daemon to pick up.

## Next
Write `state/phase.json` = `{"state": "record-close"}` and read `stages/record-close.md`.
