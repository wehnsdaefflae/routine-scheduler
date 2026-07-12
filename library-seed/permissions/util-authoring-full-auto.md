---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]
  confirm: false
---
# permission: util authoring (full auto) — create and revise without approval

Unlocks the `write_util` action with FULL autonomy: creating and revising utils are both
auto-approved once the selftest passes (every change is committed, so it stays reversible
and auditable). Autonomy raises the bar, not lowers it: check the existing catalog first
(`util name=list`) so you never duplicate a capability under a new name, pick names that
say what the util does, and report every util you created or changed in the finish
summary. The write_util action description states the full script contract. Utils are a
shared toolbox: single-purpose, reusable, never a one-off. Hold at most one util-authoring
variant.
