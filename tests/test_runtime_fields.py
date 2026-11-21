"""The RoutineConfig fields the LOADER fills are not routine.yaml keys: one found in the file is
reported as unknown and the loader's own value wins."""

import yaml

from helpers import routine_yaml
from rsched.config import load_routine
from rsched.config.routine import RUNTIME_FIELDS, TOP_KEYS, RoutineConfig


def test_the_runtime_fields_are_real_fields_and_never_keys():
    assert set(RoutineConfig.model_fields) >= RUNTIME_FIELDS
    assert not RUNTIME_FIELDS & TOP_KEYS


def test_a_runtime_field_in_routine_yaml_is_reported_and_ignored(make_routine):
    root = make_routine(slug="runtime")
    raw = {**routine_yaml(root), "deliberation": "terse", "dir": "/elsewhere"}
    (root / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg, problems = load_routine(root)
    assert cfg is not None
    assert cfg.dir == root and cfg.deliberation != "terse"
    assert [p for p in problems if "the loader fills it" in p] == [
        f"{key}: unknown routine.yaml key — the loader fills it, never this file (ignored)"
        for key in ("deliberation", "dir")]
