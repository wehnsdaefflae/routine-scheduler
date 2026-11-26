"""What a run-gate CHECK calls the job layer it watches — the one pre-D174 spelling still read.

A routine already on disk may name the job a check watches `task:` rather than `job:`, and that
config is the operator's own hand-maintained file, which no code here may rewrite. The key is
READ as the canonical one and never written back, so a routine that kept `task:` keeps working
with nothing migrated.

It lives in its own module for two reasons. The deprecation has ONE owner, so no call site can
forget a spelling (the shape `log_health_event`'s cooldown uses); and when the alias is finally
dropped, this file is what gets deleted — not a scattering of `or` clauses.

MIGRATION(expires=2027-01-15): a temporary thing declares when it is overdue, like every other
one in this repo (F699 — a deprecation here is an ALIAS TABLE rather than migrate-named code,
which is what `migration_markers` reads to notice one). The date is measured rather than chosen:
the rename shipped 2026-10-12 (0.416.0), and the two halves that kept the rest of this module
alive have since converged by themselves — every installed `routine.yaml` spells the capability
`jobs:`, and the one store that ever carried the old name has written the canonical one since
its first save after the rename, so the legacy store file, its list key and the `capabilities:`
alias are gone (F878). What is left alive is one hand-maintained config's run-gate checks, which
spell the job they watch `task:`. On the date, read that spelling and either rename it and
delete this module, or move the marker with a reason. Do NOT delete it while it stands — those
checks stop watching their jobs, and that routine's job layer goes quiet.
"""

from __future__ import annotations

#: The key a run-gate check names the job it watches with — canonical first, the deprecated
#: spelling second.
CHECK_KEY, CHECK_ALIAS = "job", "task"


def check_job(check: object) -> str:
    """The job id a run-gate check watches, under either key — "" when it watches none."""
    if not isinstance(check, dict):
        return ""
    return str(check.get(CHECK_KEY) or check.get(CHECK_ALIAS) or "")
