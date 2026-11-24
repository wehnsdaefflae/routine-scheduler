"""The token pace reading: a RATE and a month-end destination, and no ceiling it cannot see."""

from datetime import UTC, datetime

from rsched.readmodels.token_pace import MIN_ELAPSED_FRAC, fleet_pace, pace

#: Two months of spend, as `stats.monthly_spend` shapes it.
SERIES = {
    "months": ["2026-10", "2026-11"],
    "by_routine": {
        "alpha": {
            "2026-10": {"runs": 4, "tokens": 400_000, "volume": 900_000, "notional": 3.0},
            "2026-11": {"runs": 10, "tokens": 1_000_000, "volume": 4_000_000, "notional": 12.5},
        },
        "beta": {
            "2026-11": {"runs": 1, "tokens": 50_000, "volume": 60_000, "notional": 0.5},
        },
    },
}

#: Exactly half of November 2026 (30 days) elapsed.
HALFWAY = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)


def test_the_projection_is_this_months_rate_carried_to_month_end():
    """Half the month gone and a million tokens spent projects two million, not one."""
    reading = pace(SERIES, "alpha", now=HALFWAY)
    assert reading["tokens"] == 1_000_000
    assert reading["elapsed_frac"] == 0.5
    assert reading["projected_tokens"] == 2_000_000
    assert reading["projected_volume"] == 8_000_000


def test_a_month_too_young_to_extrapolate_from_offers_no_projection():
    """One heavy run on the 2nd would otherwise 'project' fifteen times itself. The count is
    still reported — it is the FORECAST that is withheld, and as None rather than 0, which would
    render as heading for nothing."""
    reading = pace(SERIES, "alpha", now=datetime(2026, 11, 2, tzinfo=UTC))
    assert reading["elapsed_frac"] < MIN_ELAPSED_FRAC
    assert reading["projected_tokens"] is None
    assert reading["projected_volume"] is None
    assert reading["tokens"] == 1_000_000


def test_growth_is_measured_against_the_previous_month():
    """`growing` compares VOLUME with last month, the same comparison and factor the card's
    spend line makes — two readings of one series must not disagree."""
    reading = pace(SERIES, "alpha", now=HALFWAY)
    assert reading["growing"] is True
    assert reading["prev_tokens"] == 400_000
    beta = pace(SERIES, "beta", now=HALFWAY)
    assert beta["growing"] is False
    assert beta["prev_month"] is None
    assert beta["prev_tokens"] is None


def test_a_routine_the_series_never_saw_reads_nothing():
    """None, not a row of zeros: a zero would claim it ran and spent nothing."""
    assert pace(SERIES, "never-ran", now=HALFWAY) is None
    assert pace({"months": [], "by_routine": {}}, "alpha", now=HALFWAY) is None


def test_a_malformed_month_key_does_not_raise():
    """The series is parsed from a durable stream, so a junk month must degrade, not 500."""
    broken = {"months": ["not-a-month"],
              "by_routine": {"alpha": {"not-a-month": {"tokens": 5, "volume": 5}}}}
    reading = pace(broken, "alpha", now=HALFWAY)
    assert reading["tokens"] == 5
    assert reading["elapsed_frac"] == 1.0
    assert reading["projected_tokens"] == 5


def test_the_fleet_reading_sums_every_routine():
    """A subscription window is spent by every routine at once, so the instance's own pace is
    the figure that anticipates running out — the cards answer a different question."""
    fleet = fleet_pace(SERIES, now=HALFWAY)
    assert fleet["tokens"] == 1_050_000
    assert fleet["volume"] == 4_060_000
    assert fleet["projected_tokens"] == 2_100_000
    assert fleet["projected_notional"] == 26.0
    assert fleet_pace({"months": [], "by_routine": {}}, now=HALFWAY) is None


def test_a_month_already_over_is_a_fact_not_a_forecast():
    """Read in December, November is complete: its projection is its own figure."""
    reading = pace(SERIES, "alpha", now=datetime(2026, 12, 9, tzinfo=UTC))
    assert reading["elapsed_frac"] == 1.0
    assert reading["projected_tokens"] == reading["tokens"]
