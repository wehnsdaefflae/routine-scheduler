"""The reminder census util (`util-seed/utils/reminder-census`) replays every reminder against the
canonical rendering of every retained action. It runs in its own interpreter and cannot import
the engine, so it carries its own copy of that rendering — pinned here to the engine's, because
a census replaying a drifted rendering reports live reminders as dead (the drift that killed 41
of 131 of them was exactly a rendering nobody checked).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from rsched.engine.actionschema import BRIEF_FIELD, KINDS, canon

SOURCE = Path(__file__).resolve().parents[1] / "util-seed/utils/reminder-census/main.py"


@pytest.fixture(scope="module")
def census():
    spec = importlib.util.spec_from_file_location("reminder_census", SOURCE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_its_identifying_fields_are_the_engines(census):
    assert census.BRIEF_FIELD == BRIEF_FIELD


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_its_rendering_is_the_engines_for_every_kind(census, kind):
    for action in ({"kind": kind}, {"kind": kind, BRIEF_FIELD.get(kind, "x"): "value"},
                   {"kind": kind, "name": "store", "args": ["stage", "--note", "x"]},
                   {"kind": kind, "paths": ["a.md", "b.md"], "command": "git push"}):
        assert census.canon(action) == canon(action), action


def test_its_own_selftest_passes(census):
    census.selftest()
