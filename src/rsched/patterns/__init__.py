"""SETTINGS PATTERNS — named, immutable settings documents in the library, each designed around
one abstract workflow, that routines FOLLOW (docs/patterns.md).

A routine's settings stay its own: `routine.yaml` records every value, plus `pattern:` naming the
pattern it follows. What the pattern adds is a reference to read the routine against — every
governed field whose value differs from the pattern's is an OVERRIDE, shown as one on the page —
and a starting point: creation, the page's "Recommend for this routine" and a pattern switch all
propose the pattern's values as PENDING changes the person accepts with one button. Nothing
resolves a pattern at load, so deleting one leaves every follower's settings exactly as they were
and only the reference goes.

- `fields.py`   — the settings vocabulary: every field a pattern may govern, its canonical form,
                  the snapshot of one routine, and the diff between two documents.
- `store.py`    — the library store: list, read, create (never edit), delete, followers.
- `drafts.py`   — pending changes awaiting the person's accept, one document per routine.
- `recommend.py`— the recommender: which pattern fits and which changes this routine needs.
"""
