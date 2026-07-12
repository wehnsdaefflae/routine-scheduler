---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]
  confirm: revisions-only
---
# permission: util authoring (autonomous revisions) — repair utils freely, new ones ask

Unlocks the `write_util` action with autonomy over REVISIONS: repairing or extending an
existing util is auto-approved once its selftest passes (still committed, so every change
is reversible). Creating a NEW util files a blocking approval question to the user first.
Repair before workaround: a util that errors mid-run is a repair opportunity — read its
source (`util` name `show`), fix it, write_util the corrected script; the fix benefits
every routine. The write_util action description states the full script contract. Utils
are a shared toolbox: single-purpose, reusable, never a one-off. Hold at most one
util-authoring variant.
