# LEDGER — Library sync

### seed — hand-authored
Publishing the instance to its library repo became a routine again in 0.165.0, replacing the
daemon job and its Settings card. The job's own docstring argued the opposite ("the exact same
commands every time, no LLM in the path") — the counter-argument is that the two commands are
not where the work is. The daemon job could run, fail to push, and say so only into a status
file nobody opens; 94 commits accumulated unpushed that way. A routine reads the outcome, keeps
a LEDGER of it, reports the failure to an owner, and finishes `failed` when the off-box copy did
not move.

Deliberately barred from repairing anything: no conflict resolution, no force-push, no
re-authentication. This repo is the only off-box copy of the instance, so the routine's job is
to notice and hand off, never to be inventive with history.
