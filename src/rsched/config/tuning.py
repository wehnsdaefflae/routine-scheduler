"""tuning.yaml — a routine's machine-tunable BEHAVIOR parameters, read and written.

Split out of `config/routine.py`, which holds the user's sealed authority config
(`routine.yaml`). The two files are different kinds of thing and only one of them a
meta-routine may edit: tuning.yaml is recipe-classed, so the routine-improver reaches it
under its fs_write_root, while every value in routine.yaml stays the user's.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..paths import atomic_write_yaml, read_yaml
from .base import DELIBERATION_LEVELS, TUNING_KEYS

TUNING_FILE = "tuning.yaml"
#: What routine.yaml's `schedule:` mapping may hold — the keys `cron`, `tz` and `catchup` load
#: from (their AliasPaths above).
def load_tuning(routine_dir: Path) -> tuple[dict, list[str]]:
    """<dir>/tuning.yaml — the routine's machine-tunable BEHAVIOR parameters (today:
    `deliberation`), recipe-classed: the routine-improver may edit it under its
    fs_write_root, while routine.yaml stays the user's sealed authority config. Absent
    file = all defaults. Returns (values, problems); unknown keys/values are reported
    and dropped, never applied.
    """
    path = routine_dir / TUNING_FILE
    if not path.is_file():
        return {}, []
    try:
        raw = read_yaml(path, {})
    except (OSError, yaml.YAMLError) as exc:
        return {}, [f"tuning.yaml: {exc}"]
    if not isinstance(raw, dict):
        return {}, ["tuning.yaml: expected a mapping at top level"]
    problems: list[str] = []
    out: dict = {}
    level = raw.pop("deliberation", None)
    if level is not None:
        if level in DELIBERATION_LEVELS:
            out["deliberation"] = level
        else:
            problems.append(f"tuning.yaml deliberation: unknown level {level!r} "
                            f"(expected one of {DELIBERATION_LEVELS})")
    # The escalation ladder's MACHINE-TUNABLE half: `n` (the interval) and the rung's own turn
    # cap. Here rather than in routine.yaml precisely so a meta-routine can raise `n` for a
    # routine whose supervisor keeps answering `continue`, on measured evidence.
    for key in [k for k in TUNING_KEYS if k != "deliberation"]:
        value = raw.pop(key, None)
        if value is None:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[key] = value
        else:
            problems.append(f"tuning.yaml {key}: expected a whole number above zero, "
                            f"got {value!r}")
    problems += [f"tuning.yaml {key}: unknown tuning key" for key in raw]
    return out, problems


def write_tuning(routine_dir: Path, updates: dict) -> list[str]:
    """Merge updates into tuning.yaml (atomic); returns the keys it DROPPED as unknown.

    Callers validate values; the web layer's controls and the creators (scaffold,
    conversations, clarify sessions) write through here.

    A value of `None` REMOVES its key rather than storing a null. A knob whose default is
    DERIVED — `oversight_turns` is `ladder_turn_interval // 2 + 1`, floored — has no literal
    value meaning "work it out again", so without a removal the only way back from a pinned
    number was editing the file by hand. Absence is that knob's one spelling of its default
    (`load_tuning` drops unknown keys and `engine/ladder` derives what is missing), so removal
    is what a control clearing the field has to produce.

    The RESULT is held to `TUNING_KEYS` — the vocabulary `load_tuning` reads — so this writer
    cannot leave behind a file the next load reports (F759). It used to merge and write
    whatever the file held, which made a RENAMED key permanent: `load_tuning` reported it after
    every single write, and no caller could clear it, since a null means "remove" only for a
    key someone can still name. A dropped key is RETURNED rather than swallowed, so the caller
    can say what it repaired (`PATCH /routines/{slug}` reports it as `repaired`) — the same
    reason the engine's file writer announces an appended separator: a change is better learned
    from the observation than from a problem line that quietly stopped appearing.
    """
    path = routine_dir / TUNING_FILE
    try:
        raw = read_yaml(path, {})
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    for key, val in updates.items():
        if val is None:
            raw.pop(key, None)
        else:
            raw[key] = val
    dropped = sorted(set(raw) - set(TUNING_KEYS))
    atomic_write_yaml(path, {k: v for k, v in raw.items() if k in TUNING_KEYS})
    return dropped
