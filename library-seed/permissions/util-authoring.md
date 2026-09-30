---
effect:
  with: writes new shared utils and repairs or extends existing ones, for every routine that calls them
  without: uses the utils that exist and reports a missing or broken one instead of fixing it
  when: it keeps needing a tool nobody has written yet, or keeps running into bugs in the ones it uses
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util, revise_util]
---
# permission: util-authoring — create and revise the shared utils

Before creating, find the util whose subject this is and add a verb to it: one subject, one util.
A revision lands on every caller at its next run, untested by them. Keep every documented
invocation working: new verbs and optional flags are safe; a renamed flag, a changed default or
narrower input breaks a pipeline you cannot see, so name what may break in your summary.
Reservation follows a verb's name (`<util>:<verb>` in a permission's `requires:`). Keep a
reserved verb's name and keep its act in that verb alone; a verb folded into a util reserved
whole becomes gated for every caller.
Name every util you created or revised in your finish summary.
