"""Subscription quota normalization and route applicability."""
from datetime import UTC, datetime

from rsched.endpoints.subscription_quota import normalize

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
RAW = {
    "five_hour": {"utilization": 39.0, "resets_at": "2026-09-05T11:10:00Z"},
    "seven_day": {"utilization": 68.4, "resets_at": "2026-09-09T00:00:00Z"},
    "seven_day_sonnet": {"utilization": 12.0},
    "unknown_window": {"utilization": 99.0},
}


def test_utilization_becomes_the_remaining_percentage_the_operator_asked_for():
    got = normalize(RAW, now=NOW)
    assert got["five_hour"]["remaining"] == 61.0        # 100 - utilization, computed once
    assert got["five_hour"]["utilization"] == 39.0
    assert got["five_hour"]["seconds_until_reset"] == 2 * 3600 + 10 * 60
    assert got["seven_day"]["remaining"] == 31.6
    # a window with no reset stamp still reports its percentage
    assert got["seven_day_sonnet"]["seconds_until_reset"] is None
    # …and a window we do not render is not invented into the payload
    assert "unknown_window" not in got

def test_quota_requires_a_configured_source(api_client):
    """The fixture endpoint is an `openai` one, so the route must say the question does not
    apply rather than reaching for a credential that could not answer it."""
    c, _tmp = api_client
    r = c.get("/api/settings/endpoints/dummy/quota")
    assert r.status_code == 200 and r.json() == {"supported": False}
    assert c.get("/api/settings/endpoints/nope/quota").status_code == 404
