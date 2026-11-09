"""Subscription quota normalization and route applicability.

TWO SHAPES, ONE REPRESENTATION (D277). `/api/oauth/usage` carried every window as a top-level
key and this module read four of them by name; the API is moving model- and surface-scoped
weekly limits into a `limits[]` array while still sending some as legacy `seven_day_<model>`
keys. A scoped limit that exists only in `limits[]` reached a reader that could not see it, so
the card's weekly Opus/Sonnet line would have disappeared with no error — **a limit we cannot
read reading as a limit that is not there**.

These tests are built from BOTH payload shapes, which is what the order asks for: legacy-only,
`limits[]`-only, both for the same model, a malformed entry, and an unknown kind.
"""
from datetime import UTC, datetime

from rsched.endpoints.subscription_quota import normalize

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)

#: The payload as it looked before `limits[]` existed.
LEGACY = {
    "five_hour": {"utilization": 39.0, "resets_at": "2026-09-05T11:10:00Z"},
    "seven_day": {"utilization": 68.4, "resets_at": "2026-09-09T00:00:00Z"},
    "seven_day_sonnet": {"utilization": 12.0},
    "unknown_window": {"utilization": 99.0},
}


def _scoped(ident, name, percent, *, dimension="model", kind="weekly_scoped", resets=""):
    return {"kind": kind, "percent": percent, "resets_at": resets,
            "scope": {dimension: {"id": ident, "display_name": name}}}


def test_the_two_unscoped_windows_become_the_remaining_percentage_asked_for():
    got = normalize(LEGACY, now=NOW)

    assert got["windows"]["five_hour"]["remaining"] == 61.0   # 100 - utilization, computed once
    assert got["windows"]["five_hour"]["utilization"] == 39.0
    assert got["windows"]["five_hour"]["seconds_until_reset"] == 2 * 3600 + 10 * 60
    assert got["windows"]["seven_day"]["remaining"] == 31.6
    # a window we do not render is not invented into the payload
    assert "unknown_window" not in got["windows"]


def test_a_legacy_weekly_key_is_read_as_a_scoped_limit():
    """`seven_day_sonnet` IS a per-model weekly limit — it was only ever spelled as a
    top-level key. Reading it as one is what lets the same limit arriving in `limits[]`
    reconcile onto it instead of producing a second, disagreeing row.
    """
    got = normalize(LEGACY, now=NOW)

    row = got["scoped"]["model:sonnet"]
    assert row["dimension"] == "model" and row["id"] == "sonnet"
    assert row["used_pct"] == 12.0 and row["remaining"] == 88.0
    assert row["seconds_until_reset"] is None, "no stamp is not a reset that just happened"


def test_a_weekly_key_for_a_model_this_build_never_heard_of_is_still_read():
    """By PREFIX, not by a fixed list: Paseo sees `seven_day_omelette`, and a list is a thing
    that silently stops covering the payload.
    """
    got = normalize({"seven_day_omelette": {"utilization": 5.0}}, now=NOW)

    assert got["scoped"]["model:omelette"]["remaining"] == 95.0


def test_a_limits_entry_is_read_with_its_own_display_name():
    """The console renders a scoped row from its OWN name, so a fixed label map is no longer
    the thing that decides whether a limit can be shown at all.
    """
    got = normalize({"limits": [_scoped("claude-opus-4", "Opus", 77.5,
                                        resets="2026-09-09T00:00:00Z")]}, now=NOW)

    row = got["scoped"]["model:claude-opus-4"]
    assert row["name"] == "Opus" and row["dimension"] == "model"
    assert row["used_pct"] == 77.5 and row["remaining"] == 22.5
    assert row["seconds_until_reset"] == 4 * 24 * 3600 - 9 * 3600


def test_a_surface_scoped_limit_is_not_mislabelled_as_a_model():
    """The API scopes a limit by model OR by surface. A reader that assumed "model" would
    print a surface limit as if a model were capped.
    """
    got = normalize({"limits": [_scoped("claude_code", "Claude Code", 40.0,
                                        dimension="surface")]}, now=NOW)

    assert got["scoped"]["surface:claude_code"]["dimension"] == "surface"


def test_a_limit_sent_both_ways_is_one_row_and_limits_wins():
    """The whole point of keying by SCOPE: during the migration the same weekly cap arrives
    twice, and two rows that disagree are worse than either. `limits[]` wins because that is
    where the upstream is going and it carries the display name and the dimension.
    """
    got = normalize({**LEGACY, "limits": [_scoped("sonnet", "Sonnet", 30.0)]}, now=NOW)

    assert len(got["scoped"]) == 1
    row = got["scoped"]["model:sonnet"]
    assert row["used_pct"] == 30.0, "the limits[] figure, not the legacy key's 12.0"
    assert row["name"] == "Sonnet", "the display name only limits[] carries"


def test_a_malformed_entry_costs_that_entry_and_never_the_windows():
    """The operator's quota readout must not go blank because one row of a moving upstream
    payload grew a field or lost one.
    """
    got = normalize({**LEGACY, "limits": [
        "not an object",
        {"kind": "weekly_scoped", "percent": 10.0},                 # no scope
        _scoped("opus", "Opus", "not a number"),                    # unreadable percent
        _scoped("haiku", "Haiku", 20.0),                            # good, and still read
    ]}, now=NOW)

    assert got["windows"]["five_hour"]["remaining"] == 61.0, "the windows survived"
    assert got["scoped"]["model:haiku"]["remaining"] == 80.0, "the good entry survived"
    assert "model:opus" not in got["scoped"]
    assert len(got["unknown"]) == 3


def test_an_entry_of_an_unknown_kind_is_reported_not_dropped():
    """Named, with no figure. A limit nobody renders is a limit nobody knows exists — the
    same reason the health stream keeps a vocabulary.
    """
    got = normalize({"limits": [_scoped("opus", "Opus", 50.0, kind="monthly_absolute")]},
                    now=NOW)

    assert got["scoped"] == {}
    assert got["unknown"] == ["Opus"]


def test_an_empty_payload_is_empty_rather_than_an_error():
    got = normalize({}, now=NOW)

    assert got == {"windows": {}, "scoped": {}, "unknown": []}


def test_the_console_renders_scoped_rows_by_name_and_says_what_it_could_not_read():
    """The card half of the increment, pinned on the module the console actually loads: a
    fixed label map cannot name a model it was not compiled with, and an unreadable limit must
    be visible as unreadable.
    """
    from pathlib import Path

    quota_js = (Path(__file__).resolve().parents[1] / "static" / "components"
                / "quota.js").read_text(encoding="utf-8")

    assert "q.scoped" in quota_js, "the scoped rows are read"
    assert "row.name" in quota_js, "…and rendered from their own names"
    assert "unreadable" in quota_js, "…and an unreadable limit says so"
    assert "seven_day_sonnet" not in quota_js, "the fixed per-model labels are gone"


def test_quota_requires_a_configured_source(api_client):
    """The fixture endpoint is an `openai` one, so the route must say the question does not
    apply rather than reaching for a credential that could not answer it."""
    c, _tmp = api_client
    r = c.get("/api/settings/endpoints/dummy/quota")
    assert r.status_code == 200 and r.json() == {"supported": False}
    assert c.get("/api/settings/endpoints/nope/quota").status_code == 404
