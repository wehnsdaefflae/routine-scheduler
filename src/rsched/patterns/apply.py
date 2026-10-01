"""A pattern's settings written INTO a routine's own files — what "saved with its pattern's
values" means at creation.

The inverse of `fields.snapshot`: every governed value goes to the key that holds it —
`routine.yaml` for most, `tuning.yaml` for the deliberation level, the finish line's own file
for the finish line. After this the routine's files say what it is; the pattern is only the
reference its page reads it against.
"""

from __future__ import annotations

from pathlib import Path


def routine_yaml(settings: dict, *, tz: str) -> dict:
    """The `routine.yaml` keys these settings set (the finish line and the deliberation level
    live elsewhere — see `finish_line` and `deliberation`).

    An `ask_first` field (`grants`) is never written here: a pattern deciding which secrets a
    routine receives would be a pattern granting them, so creation proposes it as a pending
    change instead (`recommend.at_creation`).
    """
    from ..schedule import friendly_to_cron

    out: dict = {}
    if "schedule" in settings:
        spec = settings["schedule"] or {}
        friendly = spec.get("friendly") or {"frequency": "manual"}
        out["enabled"] = friendly.get("frequency") != "disabled"
        out["schedule"] = {"cron": friendly_to_cron(friendly), "tz": tz,
                           "catchup": spec.get("catchup") or "skip"}
    if "run_gate" in settings and (settings["run_gate"] or {}).get("checks"):
        out["run_gate"] = dict(settings["run_gate"])
    renamed = {"reminders": "shared_reminders"}
    for key in ("improve", "permissions", "capabilities", "rules", "reminders", "budgets",
                "fs_read_roots", "fs_write_roots", "connections", "machines", "models", "tags"):
        if key in settings:
            out[renamed.get(key, key)] = settings[key]
    if "keep_runs" in settings:
        out["retention"] = {"keep_runs": int(settings["keep_runs"])}
    return out


def finish_line(settings: dict) -> dict | None:
    return settings.get("finish_line")


def deliberation(settings: dict) -> str:
    return str(settings.get("deliberation") or "")


def write_finish_line(routine_dir: Path, settings: dict, *, now: str) -> None:
    doc = finish_line(settings)
    if doc and (doc.get("outcomes") or doc.get("until")):
        from ..engine import finishline

        finishline.save(routine_dir, doc, now=now)
