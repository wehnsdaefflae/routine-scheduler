# LEDGER — Rules review

### seed — hand-authored
The library's general rules are improved from EVIDENCE, not from a desk: this routine reads how
runs actually interpreted each rule and revises the shared text from that evidence.
It holds rule-authoring, so it applies the revision itself under the user's approval level.
Two things stay the user's: which rules bind a routine (config), and deleting one (it would
un-bind every holder silently) — those are reported, not done.
Absorbs the routing table that used to be a rule of its own: which problem class belongs to
which owner (`stages/route-elsewhere.md`), so a non-rule finding leaves here addressed.
