"""Friendly schedule ↔ cron round-trip and descriptions."""

from pathlib import Path

import pytest

from rsched.schedule import cron_to_friendly, describe, friendly_to_cron, server_tz


@pytest.mark.parametrize(("spec", "cron"), [
    ({"frequency": "manual"}, ""),
    ({"frequency": "hourly", "minute": 15}, "15 * * * *"),
    ({"frequency": "daily", "time": "07:30"}, "30 7 * * *"),
    ({"frequency": "weekly", "time": "08:00", "weekdays": [1]}, "0 8 * * 1"),
    # F347: weekly is a SET of days — "not on weekends" round-trips as a dow list
    ({"frequency": "weekly", "time": "10:00", "weekdays": [1, 2, 3, 4, 5]},
     "0 10 * * 1,2,3,4,5"),
    ({"frequency": "monthly", "time": "06:05", "day": 3}, "5 6 3 * *"),
])
def test_friendly_cron_roundtrip(spec, cron):
    assert friendly_to_cron(spec) == cron
    back = cron_to_friendly(cron)
    assert back["frequency"] == spec["frequency"]
    for k in ("minute", "time", "weekdays", "day"):
        if k in spec:
            assert back[k] == spec[k]


def test_unrecognized_cron_is_custom():
    f = cron_to_friendly("*/5 9-17 * * 1-5")
    assert f["frequency"] == "custom" and f["cron"] == "*/5 9-17 * * 1-5"


def test_dow_range_reads_as_weekly_set():
    """F347: a hand-written '1-5' dow (how cron-literate users say weekdays) reads back
    as the same weekly SET the editor produces — not as an opaque custom cron."""
    f = cron_to_friendly("0 10 * * 1-5")
    assert f == {"frequency": "weekly", "time": "10:00", "weekdays": [1, 2, 3, 4, 5]}
    assert cron_to_friendly("0 10 * * 5,1,3")["weekdays"] == [1, 3, 5]
    # names/steps stay custom — parsing half a vocabulary would lie about the schedule
    assert cron_to_friendly("0 10 * * MON-FRI")["frequency"] == "custom"
    assert cron_to_friendly("0 10 * * */2")["frequency"] == "custom"


def test_describe():
    assert describe("") == "Manual — runs only when you click Run now"
    assert describe("0 7 * * 1") == "Every Monday at 07:00"
    assert describe("30 6 * * *") == "Every day at 06:30"
    assert describe("0 8 3 * *") == "Every month on day 3 at 08:00"
    assert describe("0 10 * * 1-5") == "Every weekday at 10:00"
    assert describe("0 10 * * 1,3,5") == "Every Monday, Wednesday and Friday at 10:00"


def test_invalid_friendly():
    with pytest.raises(ValueError):
        friendly_to_cron({"frequency": "daily", "time": "25:00"})
    with pytest.raises(ValueError):
        friendly_to_cron({"frequency": "weekly", "time": "08:00", "weekdays": [9]})
    with pytest.raises(ValueError):   # an empty set would mean "never" — refuse it loudly
        friendly_to_cron({"frequency": "weekly", "time": "08:00", "weekdays": []})
    with pytest.raises(ValueError):   # the retired single-weekday shape must not slip through
        friendly_to_cron({"frequency": "weekly", "time": "08:00", "weekday": 1})


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

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("rsched.schedule.datetime", _DT)
    assert server_tz() == "Europe/Berlin"


def test_server_tz_honors_tz_env(monkeypatch):
    """A TZ env var (how a container is told its zone) wins outright — no filesystem
    probing needed."""
    monkeypatch.setenv("TZ", ":Europe/Vienna")   # the leading colon form is valid
    assert server_tz() == "Europe/Vienna"


def test_server_tz_reads_etc_timezone_when_localtime_is_not_a_symlink(monkeypatch, tmp_path):
    """In a container, /etc/localtime is a bind-mounted FILE (the symlink trick dies) and
    /etc/timezone names the zone — server_tz falls through to it."""
    from types import SimpleNamespace

    class _Stamp:
        @staticmethod
        def astimezone():
            return SimpleNamespace(tzinfo=SimpleNamespace())   # no .key — a fixed offset

    class _DT:
        @staticmethod
        def now(_tz=None):
            return _Stamp()

    (tmp_path / "localtime").write_bytes(b"TZif2-binary-blob")     # a file, not a symlink
    (tmp_path / "timezone").write_text("Europe/Vienna\n", encoding="utf-8")

    real_path = Path

    def _fake_path(p):
        mapped = {"/etc/localtime": tmp_path / "localtime", "/etc/timezone": tmp_path / "timezone"}
        return mapped.get(str(p), real_path(p))

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("rsched.schedule.datetime", _DT)
    monkeypatch.setattr("rsched.schedule.Path", _fake_path)
    assert server_tz() == "Europe/Vienna"


def test_server_tz_degrades_to_utc_when_zone_is_undetectable(monkeypatch):
    """server_tz never raises: an unresolvable local zone falls back to 'UTC' (the
    scheduler still needs SOME zone to compute fires)."""

    class _Broken:
        @staticmethod
        def now(_tz=None):
            raise OSError("no clock")

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("rsched.schedule.datetime", _Broken)
    assert server_tz() == "UTC"
