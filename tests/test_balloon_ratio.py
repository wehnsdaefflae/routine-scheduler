"""BALLOON_RATIO is a MEASUREMENT, and this file is what keeps it one (D181).

The threshold was 1.5× from the heuristic's first day — a guess, and a wrong one: over 1063
adjacent 5-vs-5 windows of this instance's own usage stream (1594 folded depth-0 runs, 210
actors, measured 2026-10-13) ordinary run-to-run variation in TOKENS ran median 1.04, p75 1.38,
p90 2.09, p95 2.73, max 7.16. So 20.4% of windows cleared 1.5× with nothing having changed, and
`cost_trend_degraded` reported normal variation as regression. The operator chose the p95
(2.7) on 2026-10-15.

On 2026-10-17 the cost half of the heuristic moved from `tokens` (fresh + output, which leaves
the prompt cache out) to VOLUME — the whole context the model processed — and the band was
measured again on the backfilled stream: 740 adjacent windows over 76 actors, median 1.10, p75
1.42, p90 2.04, p95 2.51, max 5.76. 2.7 stays outside it; VOLUME_FLOOR is that measurement's
median absolute window-to-window change (2,179,949), rounded to 2,000,000.

Two things must stay true, and neither is visible from the constant alone:
1. the figure stays at or above the measured p95 — a future "let's catch more" tightening
   walks straight back into the noise band, and nothing but this file would say so;
2. the largest window the instance has produced still fires, so raising the bar did not buy
   quiet by going blind.

Every number below is a real window off this instance, not an invented one. The floors
(TURNS_FLOOR / VOLUME_FLOOR) are met in each ordinary case on purpose: only the RATIO can
decide them, which is what makes them a test of this constant rather than of the floors.
"""

from rsched.readmodels.run_health import (
    BALLOON_RATIO,
    TURNS_FLOOR,
    VOLUME_FLOOR,
    regression_flag,
)

# The measured distribution of ADJACENT-WINDOW variation in VOLUME, 2026-10-17.
MEASURED_WINDOWS = 740
MEASURED_P90 = 2.04
MEASURED_P95 = 2.51

# The largest adjacent window on the instance: newsletter-digest, 2026-08-17.
LARGEST_VOLUME_BEFORE = 2_923_560
LARGEST_VOLUME_AFTER = 16_825_755


def _rec(status="ok", turns=10, volume=5_000_000):
    return {"status": status, "turns": turns, "volume": volume}


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


def test_the_largest_measured_window_still_flags():
    """newsletter-digest, 2026-08-17: median volume 2,923,560 → 16,825,755 = 5.76×, the
    largest adjacent window the stream holds. A threshold raised for quiet's sake would have to
    lose this one to be wrong; it does not."""
    verdict = regression_flag(_window(volume=LARGEST_VOLUME_BEFORE),
                              _window(volume=LARGEST_VOLUME_AFTER))
    assert verdict["evaluated"] and verdict["flagged"]
    assert any("volume ballooned" in r for r in verdict["reasons"]), verdict["reasons"]
    ratio = LARGEST_VOLUME_AFTER / LARGEST_VOLUME_BEFORE
    assert ratio > BALLOON_RATIO > 1.0, f"the largest window is {ratio:.1f}×"


def test_ordinary_volume_variation_does_not_flag():
    """newsletter-digest, 2026-09-04: 8,562,720 → 16,097,275 = 1.88×, inside the measured
    band. The absolute growth (+7.5M) is several times VOLUME_FLOOR, so the floor cannot save
    this window — only the ratio decides, and it does not flag."""
    before, after = 8_562_720, 16_097_275
    assert after - before > VOLUME_FLOOR          # the floor is met; only the ratio decides
    assert 1.5 < after / before < BALLOON_RATIO
    assert not regression_flag(_window(volume=before), _window(volume=after))["flagged"]


def test_an_unmeasured_run_is_no_reading():
    """A row the backfill could not measure carries `volume: null` — it is left out of the
    median, never read as a run that cost nothing."""
    before = [*_window(volume=4_000_000), {"status": "ok", "turns": 10, "volume": None}]
    verdict = regression_flag(before, _window(volume=4_000_000))
    assert verdict["before"]["volume_median"] == 4_000_000


def test_ordinary_turn_variation_no_longer_flags():
    """predatory-publisher-tarpit, 2026-10-15: median 34 → 62 turns = 1.8×, a routine doing
    more of its ordinary work. +28 turns clears TURNS_FLOOR five times over."""
    before, after = 34, 62
    assert after - before > TURNS_FLOOR
    assert 1.5 < after / before < BALLOON_RATIO
    assert not regression_flag(_window(turns=before), _window(turns=after))["flagged"]


def test_a_window_just_over_the_bar_still_flags():
    """The p95 is a line, not a promise of silence: a window at 2.75× clears 2.7 and flags,
    floor met. Pinned so the constant's behaviour at its own edge is stated, not assumed."""
    before, after = 4_000_000, 11_000_000
    assert after / before > BALLOON_RATIO and after - before > VOLUME_FLOOR
    assert regression_flag(_window(volume=before), _window(volume=after))["flagged"]
