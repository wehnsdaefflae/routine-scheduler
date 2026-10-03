"""The escalation ladder's SETTINGS surface — the half a person edits.

Until 0.381.3 the ladder was reachable only by editing YAML by hand (operator, 2026-10-03:
"obviously it needs a complete ui"). The feature was fully visible once it fired and invisible
until then. These pin the contract the controls depend on:

- the three keys are in the settings VOCABULARY (`patterns/fields`), without which `canonical`
  RAISES and the page cannot render or compare them at all;
- they are declared on `RoutinePatch`, without which pydantic drops them before `apply_updates`
  sees them — a control that saves nothing while the page reports success;
- `ladder` is CONFIG (routine.yaml) and the two interval knobs are TUNING (tuning.yaml), which
  is the authority split the whole design rests on;
- `oversight_turns` cleared means DERIVED, not zero — and it is a REMOVAL from tuning.yaml,
  because absence is that knob's one spelling of its derived default.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from rsched.config import load_routine, write_tuning
from rsched.config.base import DEFAULT_RUNG_HEIGHT, TUNING_KEYS
from rsched.engine.ladder import MIN_OVERSIGHT_TURNS, ladder_settings, oversight_turns_for
from rsched.patterns import fields
from rsched.web.api_routine_patch import (
    TUNING_FIELDS,
    RoutinePatch,
    _apply_ladder,
    _validate_tuning,
)

LADDER_FIELDS = ("ladder", "ladder_rung_height", "oversight_turns")


def _routine(tmp_path, yaml: str = "slug: alpha\ndescription: t\n"):
    d = tmp_path / "alpha"
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml, encoding="utf-8")
    (d / "main.md").write_text("# t\n", encoding="utf-8")
    return d


# -- the vocabulary: without this the page cannot render the fields at all --------------------


def test_every_ladder_setting_is_in_the_settings_vocabulary():
    """`fields.canonical` RAISES on an unknown key — a field outside the vocabulary is one no
    page can mark and no pattern can govern, so a control for it would break the page."""
    for key in LADDER_FIELDS:
        assert key in fields.BY_KEY, key
        fields.canonical(key, None)          # must not raise


def test_the_patch_model_declares_every_tuning_key():
    """The import-time assert in api_routine_patch, restated as a test: a tuning key missing
    from RoutinePatch is dropped by pydantic before `apply_updates` sees it, and the page
    reports success for a save that did nothing."""
    assert set(TUNING_FIELDS) == set(TUNING_KEYS)
    assert not set(TUNING_KEYS) - set(RoutinePatch.model_fields)


def test_the_two_authority_classes_stay_apart():
    """`ladder` is config, the interval knobs are tuning. The split is the design: authority
    over being watched is the user's; how often a rung fires is machine-tunable."""
    assert "ladder" not in TUNING_KEYS
    assert {"ladder_rung_height", "oversight_turns"} <= set(TUNING_KEYS)
    assert fields.BY_KEY["ladder"].group == "limits"
    assert fields.BY_KEY["ladder_rung_height"].group == "models"


# -- what the page reads ----------------------------------------------------------------------


def test_the_snapshot_carries_the_defaults_a_routine_never_mentioned(tmp_path):
    cfg, _ = load_routine(_routine(tmp_path))
    snap = fields.snapshot(cfg)
    assert snap["ladder"] == {"enabled": False, "max_depth": 3}
    assert snap["ladder_rung_height"] == DEFAULT_RUNG_HEIGHT
    assert snap["oversight_turns"] is None          # None = derived, not unset


def test_a_pinned_rung_budget_and_a_derived_one_are_different_settings(tmp_path):
    """The reason the snapshot keeps None instead of filling in the derived number: a routine
    that leaves the budget to the engine is not configured the same as one that pinned the
    same value by hand — the first follows the interval when it changes."""
    assert not fields.equal("oversight_turns", None, oversight_turns_for(DEFAULT_RUNG_HEIGHT))
    d = _routine(tmp_path)
    write_tuning(d, {"oversight_turns": oversight_turns_for(DEFAULT_RUNG_HEIGHT)})
    cfg, _ = load_routine(d)
    assert fields.snapshot(cfg)["oversight_turns"] == oversight_turns_for(DEFAULT_RUNG_HEIGHT)


# -- the config half: a partial patch merges, a bad value is REFUSED --------------------------


def test_a_partial_ladder_patch_merges_over_the_stored_block():
    """The control toggles `enabled` without resending the depth, so the body alone cannot be
    judged — it is validated MERGED, like run_gate."""
    raw = {"ladder": {"enabled": True, "max_depth": 3}}
    _apply_ladder(raw, {"ladder": {"max_depth": 5}})
    assert raw["ladder"] == {"enabled": True, "max_depth": 5}


def test_switching_the_ladder_on_keeps_the_default_depth():
    raw: dict = {}
    _apply_ladder(raw, {"ladder": {"enabled": True}})
    assert raw["ladder"] == {"enabled": True, "max_depth": 3}


@pytest.mark.parametrize("patch", [
    {"max_depth": 0},                 # a ladder with no rungs is a ladder switched off
    {"max_depth": -1},
    {"max_depth": True},              # a bool is not a depth
    {"enabled": "yes"},               # the loader would read this as OFF
    {"bogus": 1},                     # a typo must not land silently
])
def test_a_bad_ladder_value_is_refused_rather_than_degraded(patch):
    """`load_routine` degrades an unreadable block toward OFF — the safe direction at load
    time, and the WRONG one for a save: a person told "saved" would get a silently disabled
    ladder. Every bad value is refused here instead."""
    with pytest.raises(HTTPException) as exc:
        _apply_ladder({}, {"ladder": dict(patch)})
    assert exc.value.status_code == 400
    assert "ladder" in str(exc.value.detail)


# -- the tuning half: the floors are the engine's own ------------------------------------------


@pytest.mark.parametrize("tuning", [
    {"ladder_rung_height": DEFAULT_RUNG_HEIGHT},
    {"oversight_turns": MIN_OVERSIGHT_TURNS},
    {"oversight_turns": 0},                      # 0 = derive it again
    {"deliberation": "terse"},
])
def test_sound_tuning_passes(tuning):
    _validate_tuning(dict(tuning))


@pytest.mark.parametrize("tuning", [
    {"ladder_rung_height": MIN_OVERSIGHT_TURNS - 1},
    {"ladder_rung_height": 0},                   # no interval to derive a budget from
    {"oversight_turns": MIN_OVERSIGHT_TURNS - 1},
    {"oversight_turns": True},
    {"deliberation": "nonsense"},
])
def test_unsound_tuning_is_refused_with_a_reason(tuning):
    with pytest.raises(HTTPException) as exc:
        _validate_tuning(dict(tuning))
    assert exc.value.status_code == 400
    assert str(exc.value.detail)


def test_clearing_the_rung_budget_removes_the_key_so_the_engine_derives_it_again(tmp_path):
    """The whole point of the 0 spelling. `patch_routine` dumps with `exclude_none`, under
    which a null reads as "not sent" — so the control sends 0 and the endpoint writes a
    REMOVAL, because absence is this knob's one spelling of its derived default.
    """
    d = _routine(tmp_path)
    write_tuning(d, {"ladder_rung_height": 30, "oversight_turns": 12})
    cfg, _ = load_routine(d)
    assert fields.snapshot(cfg)["oversight_turns"] == 12

    write_tuning(d, {"oversight_turns": None})          # what the endpoint does for 0
    assert "oversight_turns" not in (d / "tuning.yaml").read_text(encoding="utf-8")
    cfg, _ = load_routine(d)
    assert fields.snapshot(cfg)["oversight_turns"] is None
    # and the engine now derives it from the interval the user DID set
    settings = ladder_settings(type("C", (), {"routine": cfg})())
    assert settings["height"] == 30
    assert settings["oversight_turns"] == oversight_turns_for(30)


def test_write_tuning_removes_on_none_and_leaves_its_siblings(tmp_path):
    d = _routine(tmp_path)
    write_tuning(d, {"deliberation": "terse", "ladder_rung_height": 25, "oversight_turns": 9})
    write_tuning(d, {"oversight_turns": None})
    cfg, _ = load_routine(d)
    assert cfg.tuning.get("ladder_rung_height") == 25
    assert cfg.deliberation == "terse"
    assert "oversight_turns" not in cfg.tuning
