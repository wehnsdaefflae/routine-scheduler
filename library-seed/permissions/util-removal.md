---
effect:
  with: deletes a util nobody needs from the shared library, for every routine at once
  without: reports a dead util instead of removing it
  when: curating the library is the job
tags: [tool-use, utils, authoring]
requires:
  actions: [remove_util]
---
# permission: util-removal — delete a shared util

The engine refuses a removal only while another util's `calls:` names the target. Recipes,
routines' scripts, and permissions whose `requires:` names it break silently at their next run:
find them first and report each to its owner.
Fold before you delete: move the capability into a sibling, then remove the original. Deleting a
capability nothing replaced is a loss.
Name each removal in your finish summary with what now covers it.
