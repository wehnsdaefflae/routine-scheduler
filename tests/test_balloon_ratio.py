"""BALLOON_RATIO is a MEASUREMENT, and this file is what keeps it one (D181).

The threshold was 1.5× from the heuristic's first day — a guess, and a wrong one: over 1063
adjacent 5-vs-5 windows of this instance's own usage stream (1594 folded depth-0 runs, 210
actors, measured 2026-10-13) ordinary run-to-run variation runs median 1.04, p75 1.38, p90
2.09, p95 2.73, max 7.16. So 20.4% of windows cleared 1.5× with nothing having changed, and
`cost_trend_degraded` reported normal variation as regression. The operator chose the p95
(2.7) on 2026-10-15.

Two things must stay true, and neither is visible from the constant alone:
1. the figure stays at or above the measured p95 — a future "let's catch more" tightening
   walks straight back into the noise band, and nothing but this file would say so;
2. the 6.8× regression the rule was BUILT for still fires (self-audit 2026-09, 49,409 →
   338,024 tokens), so raising the bar did not buy quiet by going blind.

Every number below is a real window off this instance, not an invented one. The floors
(TURNS_FLOOR / TOKENS_FLOOR) are met in each ordinary case on purpose: only the RATIO can
decide them, which is what makes them a test of this constant rather than of the floors.
"""

from rsched.readmodels.run_health import (
    BALLOON_RATIO,
    TOKENS_FLOOR,
    TURNS_FLOOR,
    regression_flag,
)

# The measured distribution of ADJACENT-WINDOW variation, 2026-10-13 (D181's evidence).
MEASURED_WINDOWS = 1063
MEASURED_P90 = 2.09
MEASURED_P95 = 2.73

# The regression the heuristic exists for: self-audit's weekly medians, 2026-09.
REGRESSION_TOKENS_BEFORE = 49_409
REGRESSION_TOKENS_AFTER = 338_024


def _rec(status="ok", turns=10, tokens=5000):
    return {"status": status, "turns": turns, "tokens": tokens}


def _window(**kw):
    return [_rec(**kw) for _ in range(5)]


def test_ratio_stays_outside_the_measured_noise_band():
    """The constant may rise; it may not fall back inside ordinary variation.

    At the p90 (2.09) one window in ten of UNCHANGED routines clears the bar. The chosen
    figure is the p95 rounded down to one decimal, so ~5% still do — that is what a p95 is,
    and the absolute floors carry the rest. Anything below this is not a threshold, it is a
    sampling of the instance's own noise.
    """
    assert BALLOON_RATIO >= 2.7, (
        f"BALLOON_RATIO={BALLOON_RATIO} is under the measured p95 ({MEASURED_P95}) of "
        f"ordinary adjacent-window variation over {MEASURED_WINDOWS} windows — the band the "
        "old 1.5 threshold was flagging as regression (D181)")
    assert BALLOON_RATIO > MEASURED_P90


def test_the_sixty_eight_hundred_percent_case_still_flags():
    """The case the rule was built for: 49,409 → 338,024 tokens = 6.8×. A threshold raised
    for quiet's sake would have to lose this one to be wrong; it does not."""
    verdict = regression_flag(_window(tokens=REGRESSION_TOKENS_BEFORE),
                              _window(tokens=REGRESSION_TOKENS_AFTER))
    assert verdict["evaluated"] and verdict["flagged"]
    assert any("tokens ballooned" in r for r in verdict["reasons"]), verdict["reasons"]
    ratio = REGRESSION_TOKENS_AFTER / REGRESSION_TOKENS_BEFORE
    assert ratio > BALLOON_RATIO > 1.0, f"the built-for case is {ratio:.1f}×"


def test_ordinary_token_variation_no_longer_flags():
    """tv-show-tracker, 2026-10-15: 56,437 → 98,404 tokens = 1.74×, nothing changed under
    it. The absolute growth (+41,967) is twice TOKENS_FLOOR, so the floor cannot save this
    window — at 1.5× it was flagged, at 2.7× it is not."""
    before, after = 56_437, 98_404
    assert after - before > TOKENS_FLOOR          # the floor is met; only the ratio decides
    assert 1.5 < after / before < BALLOON_RATIO   # it WAS over the old threshold
    assert not regression_flag(_window(tokens=before), _window(tokens=after))["flagged"]


def test_ordinary_turn_variation_no_longer_flags():
    """predatory-publisher-tarpit, 2026-10-15: median 34 → 62 turns = 1.8×, a routine doing
    more of its ordinary work. +28 turns clears TURNS_FLOOR five times over."""
    before, after = 34, 62
    assert after - before > TURNS_FLOOR
    assert 1.5 < after / before < BALLOON_RATIO
    assert not regression_flag(_window(turns=before), _window(turns=after))["flagged"]


def test_a_window_just_over_the_p95_still_flags():
    """The p95 is a line, not a promise of silence: voice-model-trainer's 61,686 → 168,807
    (2.74×) landed just ABOVE the chosen 2.7 on the very day it was measured. Pinned so the
    constant's behaviour at its own edge is stated rather than assumed — a reader of D181's
    'three ordinary events, all below 2.7' should see that this one is not.
    """
    before, after = 61_686, 168_807
    assert after / before > BALLOON_RATIO
    assert regression_flag(_window(tokens=before), _window(tokens=after))["flagged"]
