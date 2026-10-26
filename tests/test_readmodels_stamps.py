"""`readmodels.stamps.instant` — the one way a read model compares two moments."""

from datetime import datetime, timedelta

from rsched.readmodels.stamps import at_or_after, instant


def test_an_offset_stamp_compares_as_the_moment_it_names_not_as_text():
    """`now_iso` writes the host's local time with its offset; as text, a Berlin stamp half an
    hour EARLIER than a UTC one sorts after it."""
    berlin, utc = "2026-10-01T08:00:00+02:00", "2026-10-01T06:30:00+00:00"
    assert berlin > utc
    assert instant(berlin) < instant(utc)
    assert instant(berlin) == instant("2026-10-01T06:00:00+00:00")
    assert instant(berlin).utcoffset() == timedelta(hours=2)


def test_a_naive_stamp_is_read_in_the_hosts_zone():
    naive = instant("2026-10-01T08:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert naive == datetime(2026, 10, 1, 8, 0).astimezone()


def test_what_names_no_moment_is_none():
    for raw in (None, "", "t", "not a date", 17):
        assert instant(raw) is None


def test_at_or_after_orders_stamps_across_a_dst_change():
    """F771, measured on the real stamps of 2026-10-25, the day Europe/Berlin fell back
    03:00 CEST -> 02:00 CET.

    The operator answered six audit decisions between 02:07 and 02:17 CET (+01:00); the
    report they belonged to was generated at 02:40 CEST (+02:00), i.e. 00:40 UTC — so every
    answer was 27-38 minutes LATER than the report. As TEXT every one of them sorts EARLIER,
    which read as "not answered yet": all six decisions re-presented as open cards and he
    answered them a second time, two of them differently.
    """
    generated = "2026-10-25T02:40:00+02:00"           # 00:40 UTC
    for marker in ("2026-10-25T02:07:19+01:00",       # 01:07 UTC — 27 min AFTER
                   "2026-10-25T02:17:47+01:00"):      # 01:17 UTC — 38 min AFTER
        assert marker < generated, "the text compare this guards against"
        assert instant(marker) > instant(generated)
        assert at_or_after(marker, generated) is True


def test_at_or_after_is_false_for_a_stamp_that_really_is_earlier():
    assert at_or_after("2026-10-25T01:00:00+02:00", "2026-10-25T02:40:00+02:00") is False
    # equal instants written with different offsets are at-or-after
    assert at_or_after("2026-10-25T02:40:00+02:00", "2026-10-25T00:40:00+00:00") is True


def test_a_stamp_that_names_no_moment_makes_no_claim():
    """Only POSITIVE knowledge may answer True here, because the two failure modes are not
    symmetric: this gate hides an answered decision from the operator, so a corrupt marker
    read as "answered" loses it for good, while read as "not answered" it costs one click he
    can see. The text fallback would do exactly the losing thing — `'zzz' >= '2026-…'` is
    True as text, and that was this helper's first draft.
    """
    assert at_or_after("zzz", "2026-10-25T02:40:00+02:00") is False
    assert at_or_after("2026-10-25T02:40:00+02:00", "zzz") is False
    assert at_or_after("", "") is False
    assert at_or_after(None, None) is False
