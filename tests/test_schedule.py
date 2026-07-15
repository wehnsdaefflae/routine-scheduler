"""Friendly schedule ↔ cron round-trip and descriptions."""

import pytest

from rsched.schedule import cron_to_friendly, describe, friendly_to_cron, server_tz


@pytest.mark.parametrize(("spec", "cron"), [
    ({"frequency": "manual"}, ""),
    ({"frequency": "hourly", "minute": 15}, "15 * * * *"),
    ({"frequency": "daily", "time": "07:30"}, "30 7 * * *"),
    ({"frequency": "weekly", "time": "08:00", "weekday": 1}, "0 8 * * 1"),
    ({"frequency": "monthly", "time": "06:05", "day": 3}, "5 6 3 * *"),
])
def test_friendly_cron_roundtrip(spec, cron):
    assert friendly_to_cron(spec) == cron
    back = cron_to_friendly(cron)
    assert back["frequency"] == spec["frequency"]
    for k in ("minute", "time", "weekday", "day"):
        if k in spec:
            assert back[k] == spec[k]


def test_unrecognized_cron_is_custom():
    f = cron_to_friendly("*/5 9-17 * * 1-5")
    assert f["frequency"] == "custom" and f["cron"] == "*/5 9-17 * * 1-5"


def test_describe():
    assert describe("") == "Manual — runs only when you click Run now"
    assert describe("0 7 * * 1") == "Every Monday at 07:00"
    assert describe("30 6 * * *") == "Every day at 06:30"
    assert describe("0 8 3 * *") == "Every month on day 3 at 08:00"


def test_invalid_friendly():
    with pytest.raises(ValueError):
        friendly_to_cron({"frequency": "daily", "time": "25:00"})
    with pytest.raises(ValueError):
        friendly_to_cron({"frequency": "weekly", "time": "08:00", "weekday": 9})


def test_server_tz_reports_the_local_zoneinfo_key(monkeypatch):
    """When the local zone resolves to a named IANA zone, server_tz reports that key."""
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    class _Stamp:
        @staticmethod
        def astimezone():
            return SimpleNamespace(tzinfo=ZoneInfo("Europe/Berlin"))

    class _DT:
        @staticmethod
        def now(_tz=None):
            return _Stamp()

    monkeypatch.setattr("rsched.schedule.datetime", _DT)
    assert server_tz() == "Europe/Berlin"


def test_server_tz_degrades_to_utc_when_zone_is_undetectable(monkeypatch):
    """server_tz never raises: an unresolvable local zone falls back to 'UTC' (the
    scheduler still needs SOME zone to compute fires)."""

    class _Broken:
        @staticmethod
        def now(_tz=None):
            raise OSError("no clock")

    monkeypatch.setattr("rsched.schedule.datetime", _Broken)
    assert server_tz() == "UTC"
