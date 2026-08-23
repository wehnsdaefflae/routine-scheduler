---
tags: [tool-use, utils, authoring]
requires:
  actions: [remove_util]
---
# permission: util removal — delete a global util

Unlocks `remove_util`: delete a util the library no longer needs. Separate from
`util-authoring` on purpose — writing a util adds a capability nobody had, while removing one
takes a capability away from every routine that calls it, and the second act deserves its own
decision. A routine may hold either, both, or neither.

Deletion is gated by the same approval level as `write_util`, and the engine REFUSES the
removal while any other util still declares the target on its `calls:` line — check the
catalog (`util name=list`) before proposing one. The deletion is committed to the library, so
it stays recoverable from git history.

Prefer consolidation to deletion: folding a util's capability into a sibling and removing the
original is a net simplification, while deleting an unfolded capability is a loss. Report
every removal in the finish summary, naming what now covers the gap. NEVER recreate a util the
user deleted — a slug with a deletion in the library's history is rejected; ask first, in a
blocking question, naming the util and why.
