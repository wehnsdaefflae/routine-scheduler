"""`readmodels.stamps.instant` — the one way a read model compares two moments."""

from datetime import datetime, timedelta

from rsched.readmodels.stamps import instant


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
