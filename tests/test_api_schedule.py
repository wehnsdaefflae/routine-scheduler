"""Week-strip schedule endpoint: fire enumeration, filtering, clamping, bad crons,
and the scheduled-LANE surface (D71/R313): suppressed member crons, lane fires."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from rsched import lanes, schedule_once


@pytest.fixture
def client(api_client, make_routine):
    make_routine(slug="weekly")  # cron "0 7 * * 1" via the shared fixture
    return api_client


def _set_schedule(routines, slug, **schedule):
    p = routines / slug / "routine.yaml"
    cfg = yaml.safe_load(p.read_text())
    cfg.update(schedule)
    p.write_text(yaml.safe_dump(cfg))


def test_weekly_routine_fires_in_window(client):
    c, _ = client
    data = c.get("/api/schedule/week").json()
    assert data["days"] == 7
    rows = {r["slug"]: r for r in data["routines"]}
    assert set(rows) == {"weekly"}
    # a day of back-fill + 7 days ahead: a weekly cron lands 1-2 times, all ISO-parseable
    fires = [datetime.fromisoformat(t) for t in rows["weekly"]["fires"]]
    assert 1 <= len(fires) <= 2 and not rows["weekly"]["truncated"]
    assert all(t.tzinfo is not None for t in fires)


def test_hourly_fires_and_days_clamp(client, make_routine):
    c, tmp = client
    make_routine(slug="hourly")
    _set_schedule(tmp / "routines", "hourly", schedule={"cron": "0 * * * *", "tz": "Europe/Berlin"})
    data = c.get("/api/schedule/week?days=99").json()
    assert data["days"] == 14
    rows = {r["slug"]: r for r in data["routines"]}
    assert 15 * 24 - 2 <= len(rows["hourly"]["fires"]) <= 400  # 14d + back-fill, capped


def test_manual_disabled_and_broken_are_skipped(client, make_routine):
    c, tmp = client
    for slug, patch in [("manual", {"schedule": {"cron": ""}}),
                        ("off", {"enabled": False}),
                        ("broken", {"schedule": {"cron": "not a cron"}})]:
        make_routine(slug=slug)
        _set_schedule(tmp / "routines", slug, **patch)
    rows = {r["slug"] for r in c.get("/api/schedule/week").json()["routines"]}
    assert rows == {"weekly"}


def test_scheduled_lane_suppresses_members_and_emits_lane_fires(client, make_routine):
    """D71/R313: a member of a lane WITH a cron never fires on its own — its vestigial
    cron must not be enumerated (the daemon would never honor those times) — and the
    lane's own cron rides out in its own list for the week strip's chained row."""
    c, tmp = client
    make_routine(slug="m1")
    make_routine(slug="m2")
    lanes.create(tmp / "routines", name="Nightly", cron="0 3 * * *",
                 members=[{"slug": "m1"}, {"slug": "m2"}])
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly"}
    chains = {ln["name"]: ln for ln in data["lanes"]}
    fires = [datetime.fromisoformat(t) for t in chains["Nightly"]["fires"]]
    # daily cron over a day of back-fill + 7 days ahead, all tz-aware
    assert 7 <= len(fires) <= 9 and all(t.tzinfo is not None for t in fires)
    assert not chains["Nightly"]["truncated"] and chains["Nightly"]["id"].startswith("lane-")


def test_paused_scheduled_lane_is_silent(client, make_routine):
    """A paused scheduled lane never auto-arms AND keeps suppressing its members —
    neither surface may show a fire that will not happen."""
    c, tmp = client
    make_routine(slug="pm")
    lane = lanes.create(tmp / "routines", name="Held", cron="0 3 * * *",
                        members=[{"slug": "pm"}])
    lanes.update(tmp / "routines", lane["id"], paused=True)
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly"}
    assert data["lanes"] == []


def test_unscheduled_lane_leaves_member_fires_alone(client, make_routine):
    c, tmp = client
    make_routine(slug="um")
    lanes.create(tmp / "routines", name="Loose", members=[{"slug": "um"}])
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly", "um"}
    assert data["lanes"] == []


def test_suppressed_member_with_one_shot_still_appears(client, make_routine):
    """Suppression withholds the member's cron fires only — an armed one-shot fires
    regardless of lane membership and must stay visible (fires empty, one_shots not)."""
    c, tmp = client
    make_routine(slug="os")
    lanes.create(tmp / "routines", name="Nightly", cron="0 3 * * *",
                 members=[{"slug": "os"}])
    schedule_once.arm(tmp / "routines", "os",
                      fire_at=datetime.now(UTC) + timedelta(hours=1),
                      reason="t", requested_by="test")
    rows = {r["slug"]: r for r in c.get("/api/schedule/week").json()["routines"]}
    assert rows["os"]["fires"] == [] and len(rows["os"]["one_shots"]) == 1
