---
effect:
  with: leave itself a caution that HOLDS a matching action before it runs, next time
  without: learns from a surprise only through memory and notes, which surface just-in-case
  when: this routine's actions have consequences worth one turn to reconsider
tags: [conduct, self-management, safety]
requires:
  reminders: local
---
# permission: consequence reminders — a caution that fires at the action, not at the boot

Unlocks the `remind` field: on the SAME turn you realise an action had an unintended effect,
leave a `(regex → consequence)` reminder. The regex matches the canonical one-line rendering of
an action — `util:<name> <args…>`, `shell: <command>`, `write_file path=<path>`, `edit_file
path=<path>` — and from then on, an action matching it is HELD before it runs: it does not
execute, you are shown the caution, and you decide again. Costs no turn to write, and the hold
itself is one turn. There is no passive version: a caution delivered with the observation
arrives after the consequence.

Write one only for a consequence you actually HIT, or provably nearly hit, and make the pattern
as narrow as the class of calls that can cause it — every live reminder is tested against every
action you take, and a broad pattern taxes turns that had nothing to do with it. The caution
must say what the consequence IS, not that care is needed: "mv over an existing destination
overwrites it silently — check the target first" earns its interruption, "be careful with file
moves" does not.

LABEL every fire with `remind_feedback`, which is what makes the pattern tunable: `could_not`
(the consequence was impossible for that action — tighten the regex), `would_have` (it was on
track and the hold stopped it — the reminder is earning its turns), `did` (you went ahead and it
happened), `didnt` (you went ahead and nothing bad happened). Read your own tally before adding
another: a reminder that fires often and is nearly always `could_not` or `didnt` should be
narrowed or deleted, and one that keeps recording `did` is not changing anything — sharpen the
caution or drop it.

Reminders are born LOCAL — yours alone, autonomous to write. A reminder belongs in the shared
GLOBAL store only when the same consequence would follow for ANY routine making that exact call
(it is about the util or the action itself, not about your files or your task); that store is
the library's, a bad entry there taxes every capable routine silently, and writing to it needs
the reminders capability at `global` and the user's approval at your remind_confirm level.
Unsure is local.
