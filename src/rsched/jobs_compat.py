"""What the JOB layer was called before D174 renamed it — the whole deprecation, in one file.

A routine already on disk spells this layer `task` in three places, and none of them is this
code's to rewrite: its store file (`state/tasks.json`), its `capabilities:` key, and the
`task:` key on a run-gate check. Every one of them is READ as the canonical name and never
written back, so a routine that kept TASKS keeps working with nothing migrated.

It lives in its own module for two reasons. The deprecation has ONE owner, so no call site can
forget a spelling (the shape `log_health_event`'s cooldown uses); and when the aliases are
finally dropped, this file is what gets deleted — not a scattering of `or` clauses.

MIGRATION(expires=2027-01-15): a temporary thing declares when it is overdue, like every other
one in this repo (F699 — this was the only deprecation here without a date, and the policy check
could not have noticed: `migration_markers.SHAPED` matches migrate-named code and this is none,
which is why that check now also reads an ALIAS TABLE as something that must declare an expiry).
The date is measured rather than chosen: the rename shipped 2026-10-12 (0.416.0) and the STORE
half has already converged by itself — `fau` writes `state/jobs.json` and its `state/tasks.json`
has not been written since, because the first save after the rename writes the canonical name.
What keeps this file alive is one hand-maintained config no code may rewrite: `fau/routine.yaml`
still spells the `capabilities:` key `tasks:` and names `task:` on its run-gate checks. One
quarter after the rename is time for that file to be edited in normal upkeep; on the date, read
those two spellings and either rename them and delete this module, or move the marker with a
reason. Do NOT delete it while they stand — `fau` loses its whole job layer.
"""

from __future__ import annotations

from pathlib import Path

#: The store file as it was named before the rename. Read when it is the only one present.
LEGACY_FILE = "state/tasks.json"
#: The key the list of records sat under inside that file.
LEGACY_LIST_KEY = "tasks"
#: The `capabilities:` key — canonical first, the deprecated spelling second.
SETTING_KEY, SETTING_ALIAS = "jobs", "tasks"
#: The key a run-gate check names the job it watches with.
CHECK_KEY, CHECK_ALIAS = "job", "task"


def read_path(routine_dir: Path, canonical: Path) -> Path:
    """Where the store is READ: `canonical`, or the pre-D174 file while that is the only one on
    disk. The first save after the rename writes the canonical name; the old file is left
    exactly where it is, because it is the routine's own data.
    """
    legacy = Path(routine_dir) / LEGACY_FILE
    return legacy if not canonical.exists() and legacy.exists() else canonical


def records(raw: dict) -> list:
    """The list of records in a store document, under either key."""
    return raw.get(SETTING_KEY) or raw.get(LEGACY_LIST_KEY) or []


def setting_value(caps: object) -> object | None:
    """The job-layer setting a `capabilities:` mapping names, under either spelling — or None.
    The canonical key wins when a file carries both.
    """
    if not isinstance(caps, dict):
        return None
    return next((caps[key] for key in (SETTING_KEY, SETTING_ALIAS) if key in caps), None)


def normalize_setting(raw: object, levels: tuple[str, ...], label: str,
                      out: dict) -> list[str]:
    """Fold the job-layer setting of a `capabilities:` mapping into `out` under the canonical
    key, whichever spelling the file used. Returns its problems (grants.normalize_capabilities).

    YAML 1.1 reads a bare `on`/`off` as a boolean: that is the parser's spelling of the same
    word, not a second convention, so it lands on the one canonical string.
    """
    given = setting_value(raw)
    if given is None:
        return []
    value = ("on" if given else "off") if isinstance(given, bool) else given
    if value in levels:
        out[SETTING_KEY] = value
        return []
    return [f"{label}.{SETTING_KEY} must be {' or '.join(levels)}"]


def check_job(check: object) -> str:
    """The job id a run-gate check watches, under either key — "" when it watches none."""
    if not isinstance(check, dict):
        return ""
    return str(check.get(CHECK_KEY) or check.get(CHECK_ALIAS) or "")
