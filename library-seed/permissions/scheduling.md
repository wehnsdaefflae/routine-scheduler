---
effect:
  with: arms a one-shot future run of itself, or of another routine
  without: runs only on its schedule, or when you press run
  when: the work has a 'come back to this on Thursday' shape
tags: [scheduling, automation, delegation]
requires:
  actions: [schedule_run]
---
# permission: scheduling — arm one-shot future runs

A one-shot is a whole run: it takes a run slot like a cron fire and skips the routine's
admission gate. Arm one where a single later run does the work, never a series of near-future
ones.
Write `reason` for a run with no other context: what to check and what to do with the answer.
Arm another routine only when its run is the point. A message it can read on its next scheduled
run is a `report`.
