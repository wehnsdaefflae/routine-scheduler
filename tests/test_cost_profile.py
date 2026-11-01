"""What does ONE run of this routine cost — and can that figure be trusted? (D163)

`stats.monthly_spend` answers cost per calendar MONTH, `run_health.recent_trend` answers
"has it ballooned" as a boolean. Neither answers the figure a budget is actually set in, so
`readmodels/cost_profile.py` reads the per-RUN half off the same stream, with the same
quantity (`volume`) and the same window pair — and this file pins the two properties that
make it worth reading rather than a fourth cost vocabulary:

1. it does not invent a second statistic — the medians it reports are the medians the flag
   fires on (`test_median_agrees_with_the_flags_median`), and the trend verdict is the flag's
   own, passed through;
2. it says when its own figures are understated. Until 0.494.0 a run that spawned children
   under-reported its tree by up to 40% (R3112: `history.prior_usage` dropped `subrun_end`
   at a resume boundary; an orphaned child's spend was written as `usage: {}`). The stream on
   disk still carries those rows, so a 5-run window straddling the fix compares two different
   quantities and reads as a cost improvement nobody made. Detection is arithmetic, not a
   hard-coded release date: a depth-0 parent whose own `#sub<n>` rows sum to MORE than it
   reported is understated whatever wrote it.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.readmodels.cost_profile import UNDERSTATEMENT_SLACK, cost_profile
from rsched.readmodels.run_health import REGRESSION_WINDOW

CHEAP = 1_000_000
DEAR = 20_000_000          # 20× CHEAP: clears BALLOON_RATIO and VOLUME_FLOOR together
SLUG = "trender"


def _leg(run_id: str, *, day: int, volume=CHEAP, depth: int = 0, turns: int = 10,
         status: str = "ok", notional=None, routine: str = SLUG) -> dict:
    return {"routine": routine, "run_id": run_id, "ts": f"2026-09-{day:02d}T02:00:00+02:00",
            "status": status, "turns": turns, "volume": volume, "depth": depth,
            "notional_cost": notional}


def _home(tmp_path: Path, legs: list[dict], *, slug: str = SLUG) -> Path:
    """A fake routines home: the durable stream plus a live routine dir, which is what
    `incarnations.live_runs` needs to claim the slug's runs as this routine's.
    """
    home = tmp_path / "routines"
    (home / slug).mkdir(parents=True)
    (home / slug / "routine.yaml").write_text("name: Trender\n", encoding="utf-8")
    control = home / ".control"
    control.mkdir()
    (control / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(leg) + "\n" for leg in legs), encoding="utf-8")
    return home


def _series(volumes: list[int], *, start_day: int = 1, **kw) -> list[dict]:
    return [_leg(f"{SLUG}:2026090{i}-020000", day=start_day + i, volume=v, **kw)
            for i, v in enumerate(volumes)]


# --- the per-run reading ------------------------------------------------------------------

def test_typical_names_what_a_run_of_this_routine_costs(tmp_path):
    """The headline figure: the median volume over EVERY run, not over a flagged window."""
    home = _home(tmp_path, _series([1, 2, 3, 4, 5]))
    profile = cost_profile(home, SLUG)
    assert profile["runs"] == 5
    assert profile["typical"]["volume_median"] == 3
    assert profile["typical"]["volume_max"] == 5


def test_another_routines_runs_are_not_this_routines_cost(tmp_path):
    legs = [*_series([1, 2, 3]),
            _leg("other:20260910-020000", day=10, volume=DEAR, routine="other")]
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["runs"] == 3
    assert profile["typical"]["volume_max"] == 3


def test_an_unmeasured_run_is_no_reading_never_a_zero(tmp_path):
    """A null volume must not drag the median to the floor — it is absent, not free."""
    profile = cost_profile(_home(tmp_path, _series([10, None, 20])), SLUG)
    assert profile["typical"]["runs"] == 3
    assert profile["typical"]["measured"] == 2
    assert profile["typical"]["volume_median"] == 15


def test_a_malformed_volume_is_skipped_as_a_reading(tmp_path):
    """One bad row never 500s a dashboard and never counts as 0 (stats.py's own rule).

    This also covers the trend hand-off: `run_health.regression_flag` does
    `float(r["volume"])` behind an `is not None` guard and therefore raises TypeError on a
    row like this one — a live fragility of that shared module (reachable from the routine
    page's `recipe_regression`, reported separately, NOT fixed here). `cost_profile` hands it
    a sanitized copy, so asking this reader for a profile must not raise.
    """
    legs = _series([10, 20])
    legs.append(_leg(f"{SLUG}:20260920-020000", day=20, volume={"not": "a number"}))
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["typical"]["measured"] == 2
    assert profile["typical"]["volume_median"] == 15
    assert profile["trend"]["evaluated"] is False      # the verdict survived the bad row


def test_notional_cost_is_carried_and_counted_separately(tmp_path):
    legs = _series([1, 2, 3])
    legs[0]["notional_cost"] = 0.5
    legs[2]["notional_cost"] = 1.5
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["typical"]["notional_priced"] == 2
    assert profile["typical"]["notional_median"] == 1.0


# --- one heuristic, not two ----------------------------------------------------------------

def test_median_agrees_with_the_flags_median(tmp_path):
    """The whole point of extending rather than adding: the windows this module reports are
    the pair `recent_trend` judged, so the page and the profile can never disagree.
    """
    home = _home(tmp_path, _series([CHEAP] * 5 + [DEAR] * 5))
    profile = cost_profile(home, SLUG)
    trend = profile["trend"]
    assert trend["flagged"] is True
    assert profile["baseline"]["volume_median"] == trend["before"]["volume_median"]
    assert profile["recent"]["volume_median"] == trend["after"]["volume_median"]
    assert profile["recent"]["runs"] == REGRESSION_WINDOW


def test_the_trend_verdict_is_passed_through_not_re_derived(tmp_path):
    """Too few runs to judge: the profile still reports cost, and reports `evaluated` false
    rather than inventing a verdict of its own.
    """
    profile = cost_profile(_home(tmp_path, _series([CHEAP] * 2)), SLUG)
    assert profile["trend"]["evaluated"] is False
    assert profile["trend"]["flagged"] is False
    assert profile["typical"]["volume_median"] == CHEAP
    assert profile["baseline"]["runs"] == 0


# --- accounting integrity (R3112) ----------------------------------------------------------

def _with_children(run_id: str, *, day: int, parent_volume: int,
                   child_volumes: list[int]) -> list[dict]:
    legs = [_leg(run_id, day=day, volume=parent_volume)]
    legs += [_leg(f"{run_id}#sub{n}", day=day, volume=v, depth=1)
             for n, v in enumerate(child_volumes, start=1)]
    return legs


def test_a_parent_reporting_less_than_its_children_is_understated(tmp_path):
    """The R3112 symptom, read off arithmetic: 10M reported over 15M of children."""
    legs = _with_children(f"{SLUG}:20260901-020000", day=1, parent_volume=10_000_000,
                          child_volumes=[9_000_000, 6_000_000])
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["integrity"]["understated_runs"] == 1
    assert profile["integrity"]["checked_runs"] == 1
    assert profile["typical"]["trustworthy"] is False
    assert "0.494.0" in profile["integrity"]["detail"]


def test_a_parent_that_folds_its_children_in_is_sound(tmp_path):
    """The post-fix shape: the parent carries its children plus its own turns."""
    legs = _with_children(f"{SLUG}:20260901-020000", day=1, parent_volume=16_000_000,
                          child_volumes=[9_000_000, 6_000_000])
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["integrity"]["understated_runs"] == 0
    assert profile["integrity"]["with_children"] == 1
    assert profile["typical"]["trustworthy"] is True
    assert "no figure here is understated" in profile["integrity"]["detail"]


def test_a_childless_run_is_never_understated(tmp_path):
    """No children, nothing to under-report — a cheap run is not a defect."""
    profile = cost_profile(_home(tmp_path, _series([1, 2, 3])), SLUG)
    assert profile["integrity"]["understated_runs"] == 0
    assert profile["integrity"]["with_children"] == 0
    assert profile["typical"]["trustworthy"] is True


def test_rounding_slack_is_not_understatement(tmp_path):
    """A shortfall inside UNDERSTATEMENT_SLACK is rounding, not a missing subtree."""
    children = 10_000_000
    legs = _with_children(f"{SLUG}:20260901-020000", day=1,
                          parent_volume=int(children * (1 - UNDERSTATEMENT_SLACK / 2)),
                          child_volumes=[children])
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["integrity"]["understated_runs"] == 0


def test_nested_children_fold_onto_the_root_run(tmp_path):
    """A grandchild's spend belongs to the tree: `<run>#sub1#sub2` counts against `<run>`."""
    run_id = f"{SLUG}:20260901-020000"
    legs = [_leg(run_id, day=1, volume=5_000_000),
            _leg(f"{run_id}#sub1", day=1, volume=4_000_000, depth=1),
            _leg(f"{run_id}#sub1#sub2", day=1, volume=4_000_000, depth=2)]
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["recent"]["runs_detail"][0]["child_volume"] == 8_000_000
    assert profile["integrity"]["understated_runs"] == 1


def test_a_window_straddling_the_fix_is_named_as_mixed(tmp_path):
    """The reading this module exists to prevent: five understated runs then five sound ones
    looks like a cost improvement. The profile names WHICH side is contaminated, so a
    consumer cannot render the comparison as one quantity.
    """
    legs: list[dict] = []
    for i in range(REGRESSION_WINDOW):        # old rows: parents short of their children
        legs += _with_children(f"{SLUG}:2026090{i}-020000", day=1 + i,
                               parent_volume=10_000_000, child_volumes=[15_000_000])
    for i in range(REGRESSION_WINDOW):        # new rows: parents fold their children in
        legs += _with_children(f"{SLUG}:2026091{i}-020000", day=11 + i,
                               parent_volume=16_000_000, child_volumes=[15_000_000])
    profile = cost_profile(_home(tmp_path, legs), SLUG)
    assert profile["mixed_accounting"] == ["baseline"]
    assert profile["baseline"]["trustworthy"] is False
    assert profile["recent"]["trustworthy"] is True
    assert profile["integrity"]["understated_runs"] == REGRESSION_WINDOW


def test_a_wholly_sound_history_names_no_mixed_side(tmp_path):
    profile = cost_profile(_home(tmp_path, _series([CHEAP] * 10)), SLUG)
    assert profile["mixed_accounting"] == []
    assert profile["recent"]["trustworthy"] is True
