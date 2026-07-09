"""Friendly schedule ↔ cron round-trip and descriptions."""

import pytest

from rsched.schedule import cron_to_friendly, describe, friendly_to_cron, server_tz


@pytest.mark.parametrize("spec,cron", [
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


def test_server_tz_is_a_string():
    assert isinstance(server_tz(), str) and server_tz()
