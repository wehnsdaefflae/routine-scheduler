"""The routine card's schedule fields say when the scheduler STARTS the routine (D71).

A member of a scheduled lane is fired by the lane, so its own cron — empty, or suppressed — names
no time it runs at. Read from that cron, every lane member's page said "Manual — runs only when
you click Run now" beside a lane tile saying the lane fires it.
"""

from __future__ import annotations

from rsched import lanes, schedule


def _card(c, slug: str) -> dict:
    return next(r for r in c.get("/api/routines").json() if r["slug"] == slug)


def test_a_lane_member_reports_the_lanes_schedule_and_next_fire(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="member")                 # carries a cron of its own: 0 7 * * 1
    home = tmp / "routines"
    assert _card(c, "member")["schedule_desc"] == schedule.describe("0 7 * * 1")

    lane = lanes.create(home, name="Morning", members=[{"slug": "member"}])
    lanes.update(home, lane["id"], cron="0 5 * * *", tz="UTC")
    sched = c.app.state.scheduler
    sched.rescan()
    card = _card(c, "member")
    assert card["schedule_desc"] == f"Lane “Morning” — {schedule.describe('0 5 * * *')}"
    assert card["next_fire"] == sched.lane_next_fires[lane["id"]].isoformat()
    # its own cron is still its config: only the schedule it is REPORTED under moved
    assert card["cron"] == "0 7 * * 1"
    # the routine page's hero reads the detail payload, which is built on the same card
    detail = c.get("/api/routines/member").json()
    assert (detail["schedule_desc"], detail["next_fire"]) == (card["schedule_desc"],
                                                              card["next_fire"])


def test_a_paused_lane_or_a_switched_off_member_has_no_next_fire(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="night-a")
    make_routine(slug="night-b")
    home = tmp / "routines"
    paused = lanes.create(home, name="Night", members=[{"slug": "night-a"}])
    lanes.update(home, paused["id"], cron="0 2 * * *", tz="UTC", paused=True)
    live = lanes.create(home, name="Day", members=[{"slug": "night-b"}])
    lanes.update(home, live["id"], cron="0 9 * * *", tz="UTC")
    assert c.patch("/api/routines/night-b", json={"enabled": False}).status_code == 200
    c.app.state.scheduler.rescan()

    assert _card(c, "night-a")["schedule_desc"] == "Lane “Night” — lane paused"
    assert _card(c, "night-a")["next_fire"] is None
    # the chain skips a switched-off member, so its lane's next fire is not its own
    off = _card(c, "night-b")
    assert off["schedule_desc"].startswith("Lane “Day” — ")
    assert off["next_fire"] is None


def test_an_unscheduled_lane_leaves_the_routines_own_schedule(api_client, make_routine):
    """A lane without a cron suppresses nothing, so its member still fires on its own cron."""
    c, tmp = api_client
    make_routine(slug="adhoc")
    lanes.create(tmp / "routines", name="Adhoc", members=[{"slug": "adhoc"}])
    sched = c.app.state.scheduler
    sched.rescan()
    card = _card(c, "adhoc")
    assert card["schedule_desc"] == schedule.describe("0 7 * * 1")
    assert card["next_fire"] == sched.next_fires["adhoc"].isoformat()
