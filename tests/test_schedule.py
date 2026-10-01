"""Friendly schedule ↔ cron round-trip and descriptions."""

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


@pytest.mark.parametrize("spec", [
    {"frequency": "hourly", "minute": None},
    {"frequency": "monthly", "time": "07:00", "day": None},
    {"frequency": "weekly", "time": "07:00", "weekdays": [None]},
    {"frequency": "weekly", "time": "07:00", "weekdays": ["mon"]},
    "daily",
])
def test_every_malformed_spec_is_a_value_error(spec):
    """The routine PATCH answers 400 for the ValueError this promises and catches nothing
    else, so a null field (TypeError) or a spec that is no mapping (AttributeError) was a 500."""
    with pytest.raises(ValueError):
        friendly_to_cron(spec)


@pytest.fixture
def etc(monkeypatch, tmp_path):
    """The two /etc files server_tz reads, pointed at tmp — absent until a test writes them."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("rsched.schedule.ETC_TIMEZONE", tmp_path / "timezone")
    monkeypatch.setattr("rsched.schedule.ETC_LOCALTIME", tmp_path / "localtime")
    return tmp_path


def test_server_tz_honors_tz_env(monkeypatch, etc):
    """A TZ env var (how a container is told its zone) wins outright — no filesystem
    probing needed."""
    (etc / "timezone").write_text("Europe/Berlin\n", encoding="utf-8")
    monkeypatch.setenv("TZ", ":Europe/Vienna")   # the leading colon form is valid
    assert server_tz() == "Europe/Vienna"


def test_server_tz_reads_etc_timezone_when_localtime_is_not_a_symlink(etc):
    """In a container, /etc/localtime is a bind-mounted FILE (the symlink trick dies) and
    /etc/timezone names the zone — server_tz falls through to it."""
    (etc / "localtime").write_bytes(b"TZif2-binary-blob")          # a file, not a symlink
    (etc / "timezone").write_text("Europe/Vienna\n", encoding="utf-8")
    assert server_tz() == "Europe/Vienna"


def test_server_tz_reads_the_localtime_symlinks_target(etc):
    """With no /etc/timezone, the symlink's TARGET names the zone (it need not exist here:
    the key is read off the path under `zoneinfo/`)."""
    (etc / "localtime").symlink_to(etc / "usr" / "share" / "zoneinfo" / "Europe" / "Vienna")
    assert server_tz() == "Europe/Vienna"


@pytest.mark.parametrize("tz", [":{localtime}", "CET-1CEST,M3.5.0,M10.5.0/3",
                                "Not/AZone", "Europe/"])
def test_a_tz_no_zoneinfo_loads_falls_through_to_the_next_source(monkeypatch, etc, tz):
    """The answer is WRITTEN beside every cron the console saves, so a TZ value ZoneInfo cannot
    load — the systemd idiom `:/etc/localtime` where that is a bind-mounted plain FILE (a
    container's), a POSIX rule, a typo — must not win. It used to be returned verbatim,
    poisoning every routine.yaml saved after it."""
    (etc / "localtime").write_bytes(b"TZif2-binary-blob")
    (etc / "timezone").write_text("Europe/Vienna\n", encoding="utf-8")
    monkeypatch.setenv("TZ", tz.format(localtime=etc / "localtime"))
    assert server_tz() == "Europe/Vienna"


def test_a_tz_naming_a_zoneinfo_path_reads_as_its_key(monkeypatch, etc):
    monkeypatch.setenv("TZ", ":/usr/share/zoneinfo/America/New_York")
    assert server_tz() == "America/New_York"


def test_server_tz_degrades_to_utc_when_zone_is_undetectable(etc):
    """server_tz never raises: no source naming a loadable zone falls back to 'UTC' (the
    scheduler still needs SOME zone to compute fires)."""
    assert server_tz() == "UTC"
    (etc / "timezone").write_text("Mars/Olympus_Mons\n", encoding="utf-8")
    (etc / "localtime").symlink_to(etc / "localtime")                # a symlink loop
    assert server_tz() == "UTC"
