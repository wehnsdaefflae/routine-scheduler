"""Week-strip schedule endpoint: fire enumeration, filtering, clamping, bad crons,
and the scheduled-group surface (D71/R313): suppressed member crons, group fires."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from rsched import groups, schedule_once


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


def test_scheduled_group_suppresses_members_and_emits_group_fires(client, make_routine):
    """D71/R313: a member of a group WITH a cron never fires on its own — its vestigial
    cron must not be enumerated (the daemon would never honor those times) — and the
    group's own cron rides out under `groups` for the week strip's chained lane."""
    c, tmp = client
    make_routine(slug="m1")
    make_routine(slug="m2")
    groups.create(tmp / "routines", name="Nightly", cron="0 3 * * *",
                  members=[{"slug": "m1", "split": False}, {"slug": "m2", "split": True}])
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly"}
    grows = {g["name"]: g for g in data["groups"]}
    fires = [datetime.fromisoformat(t) for t in grows["Nightly"]["fires"]]
    # daily cron over a day of back-fill + 7 days ahead, all tz-aware
    assert 7 <= len(fires) <= 9 and all(t.tzinfo is not None for t in fires)
    assert not grows["Nightly"]["truncated"] and grows["Nightly"]["id"].startswith("grp-")


def test_paused_scheduled_group_is_silent(client, make_routine):
    """A paused scheduled group never auto-arms AND keeps suppressing its members —
    neither surface may show a fire that will not happen."""
    c, tmp = client
    make_routine(slug="pm")
    g = groups.create(tmp / "routines", name="Held", cron="0 3 * * *",
                      members=[{"slug": "pm", "split": False}])
    groups.update(tmp / "routines", g["id"], paused=True)
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly"}
    assert data["groups"] == []


def test_unscheduled_group_leaves_member_fires_alone(client, make_routine):
    c, tmp = client
    make_routine(slug="um")
    groups.create(tmp / "routines", name="Loose", members=[{"slug": "um", "split": False}])
    data = c.get("/api/schedule/week").json()
    assert {r["slug"] for r in data["routines"]} == {"weekly", "um"}
    assert data["groups"] == []


def test_suppressed_member_with_one_shot_still_appears(client, make_routine):
    """Suppression withholds the member's cron fires only — an armed one-shot fires
    regardless of group membership and must stay visible (fires empty, one_shots not)."""
    c, tmp = client
    make_routine(slug="os")
    groups.create(tmp / "routines", name="G", cron="0 3 * * *",
                  members=[{"slug": "os", "split": False}])
    schedule_once.arm(tmp / "routines", "os",
                      fire_at=datetime.now(UTC) + timedelta(hours=1),
                      reason="t", requested_by="test")
    rows = {r["slug"]: r for r in c.get("/api/schedule/week").json()["routines"]}
    assert rows["os"]["fires"] == [] and len(rows["os"]["one_shots"]) == 1
