---
tags: [self-management]
---
# fragment: LEDGER discipline — the routine's memory of its own changes

LEDGER.md is the append-only change journal. It is how a run avoids re-exploring dead ends
and re-proposing rejected ideas — **check it before exploring anything new**.

- **Append one entry per run**, before finishing:
  `### <run id> — <one-line summary>` followed by short bullets: what changed (data /
  process / outward act), why, decisions taken, and **candidates rejected + why** (negative
  evidence is as valuable as the change itself).
- Entries record the *why behind the current state*; the files themselves stay present-tense
  (see the hygiene fragment). History lives here and in git, never inline in specs.
- **Rotation:** when LEDGER.md exceeds ~40 entries, move the oldest ~30 into
  `archive/ledger-<date>.md` and fold their gist into a few summary lines at the top of
  LEDGER.md. Git history is the real archive — prune freely; nothing is lost.
- The engine commits your working directory (LEDGER included) automatically at run end —
  you never run git yourself.
