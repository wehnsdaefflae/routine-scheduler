"""The `--model` / `--effort` role overrides of `run-once` and `engine-run`.

`kind=catalog-name` and `kind=level` pairs, repeatable per chat role. Split out of `cli.py`
(one responsibility per file): this is the one place the two spellings are parsed, so a trial's
engine subprocess and a hand-started run read them the same way.
"""

from __future__ import annotations

from .config import EFFORT_LEVELS, MODEL_KINDS, ROUTINE_MODEL_ROLES


def parse_model_overrides(values: list[str]) -> dict[str, str]:
    """--model main=gpt-4o (a catalog model NAME; repeatable per role)."""
    out: dict[str, str] = {}
    for val in values or []:
        kind, _, name = val.partition("=")
        if not (kind and name):
            raise SystemExit(f"--model expects kind=name (a catalog model), got {val!r}")
        if kind not in ROUTINE_MODEL_ROLES:
            raise SystemExit(f"--model kind must be one of {ROUTINE_MODEL_ROLES}, got {kind!r}")
        out[kind] = name
    return out


def parse_effort_overrides(values: list[str]) -> dict[str, str]:
    """--effort main=high (a reasoning level; repeatable per chat role)."""
    out: dict[str, str] = {}
    for val in values or []:
        kind, _, level = val.partition("=")
        if kind not in MODEL_KINDS or level not in EFFORT_LEVELS:
            raise SystemExit(f"--effort expects kind=level, kind one of {MODEL_KINDS} and "
                             f"level one of {EFFORT_LEVELS}, got {val!r}")
        out[kind] = level
    return out
