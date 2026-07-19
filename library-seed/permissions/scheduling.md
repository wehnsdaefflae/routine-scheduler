---
tags: [scheduling, automation, delegation]
requires:
  actions: [schedule_run]
---
# permission: scheduling — arm one-shot future runs

Unlocks the `schedule_run` action: arm a **one-shot** run of a routine at a specific future
instant, then never again — the case between cron (repeats forever) and a manual run (now).
Give `target` (the routine slug — **your own is always allowed**; another routine is the
cross-routine case this permission authorizes), `fire_at` (an absolute ISO-8601 UTC instant,
or a relative offset like `+3d` / `+2h` / `+30m`), and `reason` (a provenance line the engine
injects into the target's inbox just before it fires, so the fired run knows why it woke).

The daemon fires the one-shot **once** at `fire_at`, then **consumes** it — there is no
repeating trigger to remember to delete, and nothing rewrites the target's `routine.yaml`
(config stays the user's). Reach for it to schedule your own follow-up (*"re-check the seat in
3 days"*) or to arm a milestone run on a sibling routine after some condition. Cancel an armed
one-shot with `cancel: true` (plus `id` to cancel one, or without an id to clear every armed
one-shot on the target) — arming is durable, so a cancel before `fire_at` is the way to call
it off.

Arm deliberately: a one-shot spends a real run slot when it fires (subject to the same
one-run-per-routine, `max_concurrent_runs`, and restart-drain rules as a cron fire). Don't arm
a flurry of near-future one-shots where a single run would do, and always give a `reason` the
woken run can act on with zero other context. A `fire_at` in the past or more than a year out
is rejected; a missed one-shot (the daemon was down at its instant) fires once on the next
daemon start — the point is it *eventually* runs once.
