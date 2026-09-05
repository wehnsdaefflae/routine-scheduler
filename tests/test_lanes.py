"""LANES — the temporal axis: the store (rsched.lanes), its CRUD API (web.api_lanes), the lane
schedule (D71), the whole-lane pause, and the member-record shape.

These tests pin the store's shape guarantees (ordered/deduped member records, on_failure
vocabulary, the update tri-state), the at-most-one-lane cardinality the whole axis is built
around, and the API's member-existence validation against the live registry — which covers
only the slugs a caller ADDS, so one out-of-band routine deletion cannot lock a lane against
every further edit (F442). `rsched validate`'s two instance-level cases sit here as well — a
phantom member, a scheduled lane with nobody in it — because nothing cascades a routine
deletion out of the store. Arming a chain appears only as far as the API reaches; the
sequential advance itself is tests/test_lane_runs.py.

The shared config block and the shared store are NOT here: they are a DOMAIN with their own
file (tests/test_domains.py), for the same reason they have their own module.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched import lanes


def m(slug: str) -> dict:
    """A member record — the canonical membership shape."""
    return {"slug": slug}


# -- store ---------------------------------------------------------------------------------

def test_store_empty_reads_as_default(tmp_path):
    home = tmp_path
    data = lanes.load(home)
    assert data == {"default_on_failure": "stop", "lanes": []}
    assert not lanes.lanes_file(home).exists()   # a pure read never writes


def test_create_preserves_order_and_dedups_members(tmp_path):
    home = tmp_path
    rec = lanes.create(home, name="Morning",
                       members=[m("b"), m("a"), m("b"), {"slug": ""}, m("c")])
    # order kept, dupes + blanks dropped
    assert rec["members"] == [m("b"), m("a"), m("c")]
    assert rec["id"].startswith("lane-")            # what a NEW lane gets; no caller parses it
    assert rec["on_failure"] is None               # inherit by default
    assert lanes.get(home, rec["id"]) == rec
    assert lanes.member_slugs(rec) == ["b", "a", "c"]


def test_create_rejects_blank_name_and_bad_on_failure(tmp_path):
    home = tmp_path
    for bad in ("", "   "):
        try:
            lanes.create(home, name=bad)
            raise AssertionError("expected ValueError for blank name")
        except ValueError:
            pass
    try:
        lanes.create(home, name="ok", on_failure="explode")
        raise AssertionError("expected ValueError for bad on_failure")
    except ValueError:
        pass


def test_a_routine_belongs_to_at_most_one_lane(tmp_path):
    """The cardinality the whole axis exists for, enforced in the STORE rather than left to
    the UI to remember. A routine in two scheduled lanes fires twice; no ordering and no
    on_failure policy can undo that. Timing is therefore a record of its own, apart from the
    shared config block, whose cardinality is a different question entirely.

    Both writers check it and both name the offending slugs, because "already in another lane"
    is actionable where "invalid" is not.
    """
    home = tmp_path
    morning = lanes.create(home, name="Morning", members=[m("a"), m("b")])

    # create refuses a slug another lane already holds and names exactly that slug
    with pytest.raises(ValueError, match=r"already claimed: b$"):
        lanes.create(home, name="Nightly", members=[m("b"), m("c")])
    assert [rec["name"] for rec in lanes.list_lanes(home)] == ["Morning"]  # nothing written

    # update refuses the same way and leaves the lane it refused untouched
    nightly = lanes.create(home, name="Nightly", members=[m("c")])
    with pytest.raises(ValueError, match=r"already claimed: a$"):
        lanes.update(home, nightly["id"], members=[m("c"), m("a")])
    assert lanes.member_slugs(lanes.get(home, nightly["id"])) == ["c"]

    # …but a lane resubmitting its OWN members is an ordinary edit: `keep=` exempts the lane
    # being patched, or every reorder would refuse itself (both the routine page and the
    # dashboard PATCH the whole member list, including the members they are not changing).
    lanes.update(home, morning["id"], members=[m("b"), m("a")])
    assert lanes.member_slugs(lanes.get(home, morning["id"])) == ["b", "a"]
    assert lanes.lane_of(home, "a")["id"] == morning["id"]      # singular, because it can be


def test_update_on_failure_tristate(tmp_path):
    home = tmp_path
    rec = lanes.create(home, name="G", on_failure="continue")
    assert rec["on_failure"] == "continue"
    # omit on_failure → unchanged; rename only
    lanes.update(home, rec["id"], name="G2")
    assert lanes.get(home, rec["id"])["on_failure"] == "continue"
    assert lanes.get(home, rec["id"])["name"] == "G2"
    # explicit None → inherit
    lanes.update(home, rec["id"], on_failure=None)
    assert lanes.get(home, rec["id"])["on_failure"] is None
    # explicit value → override
    lanes.update(home, rec["id"], on_failure="stop")
    assert lanes.get(home, rec["id"])["on_failure"] == "stop"


def test_set_default_and_delete(tmp_path):
    home = tmp_path
    assert lanes.default_on_failure(home) == "stop"
    lanes.set_default_on_failure(home, "continue")
    assert lanes.default_on_failure(home) == "continue"
    rec = lanes.create(home, name="G")
    assert lanes.delete(home, rec["id"]) is True
    assert lanes.delete(home, rec["id"]) is False   # idempotent
    assert lanes.get(home, rec["id"]) is None
    # deleting a lane leaves the default intact
    assert lanes.default_on_failure(home) == "continue"


def test_load_normalizes_corrupt_shape(tmp_path):
    home = tmp_path
    from rsched.paths import atomic_write_json
    atomic_write_json(lanes.lanes_file(home),
                      {"default_on_failure": "nonsense",
                       "lanes": [{"id": "lane-x", "name": "G",
                                  "members": [{"slug": "a", "split": "yes"}, {"slug": "a"},
                                              "junk-string", 42],
                                  "on_failure": "bad"}, "junk", 42]})
    data = lanes.load(home)
    assert data["default_on_failure"] == "stop"            # bad default → built-in
    assert len(data["lanes"]) == 1                          # non-dicts dropped
    # deduped by slug (first record wins), junk entries + stray keys dropped
    assert data["lanes"][0]["members"] == [m("a")]
    assert data["lanes"][0]["on_failure"] is None          # bad value → inherit


# -- API -----------------------------------------------------------------------------------

def _mk(tmp_path: Path, slug: str) -> None:
    d = tmp_path / "routines" / slug
    (d / "state").mkdir(parents=True)
    (d / "inbox").mkdir()
    import yaml
    cfg = {"name": f"Test {slug}", "slug": slug, "enabled": True,
           "description": "t", "schedule": {"cron": "0 7 * * 1", "tz": "UTC", "catchup": "skip"},
           "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
           "budgets": {"max_turns": 5, "max_wall_clock_min": 5, "max_total_tokens": 1000,
                       "max_subruns": 1, "max_subrun_depth": 1, "ask_timeout_min": 1}}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n1. do it\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER\n", encoding="utf-8")


def test_api_lane_lifecycle(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "beta")

    # empty list carries the default + vocab + the routine picker
    r = client.get("/api/lanes")
    assert r.status_code == 200
    body = r.json()
    assert body["default_on_failure"] == "stop"
    assert body["on_failure_vocab"] == ["stop", "continue"]
    assert body["lanes"] == []
    assert {k["slug"] for k in body["known_routines"]} == {"alpha", "beta"}

    # create with real members (records)
    r = client.post("/api/lanes", json={"name": "Morning",
                                        "members": [{"slug": "beta"}, {"slug": "alpha"}]})
    assert r.status_code == 200, r.text
    lane_id = r.json()["lane"]["id"]
    assert r.json()["lane"]["members"] == [m("beta"), m("alpha")]

    # unknown member is rejected, naming the slug
    r = client.post("/api/lanes", json={"name": "Bad", "members": [{"slug": "ghost"}]})
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]

    # patch: reorder + set on_failure override
    r = client.patch(f"/api/lanes/{lane_id}",
                     json={"members": [{"slug": "alpha"}, {"slug": "beta"}],
                           "on_failure": "continue", "set_on_failure": True})
    assert r.status_code == 200, r.text
    assert r.json()["lane"]["members"] == [m("alpha"), m("beta")]
    assert r.json()["lane"]["on_failure"] == "continue"

    # set the instance default
    r = client.put("/api/lanes/default", json={"default_on_failure": "continue"})
    assert r.status_code == 200
    assert client.get("/api/lanes").json()["default_on_failure"] == "continue"

    # a bad default value is a 400
    assert client.put("/api/lanes/default",
                      json={"default_on_failure": "explode"}).status_code == 400

    # delete, then a second delete 404s
    assert client.delete(f"/api/lanes/{lane_id}").status_code == 200
    assert client.delete(f"/api/lanes/{lane_id}").status_code == 404
    assert client.get("/api/lanes").json()["lanes"] == []


def test_the_lane_api_carries_no_config_field(api_client):
    """A lane carries timing and nothing else: the shared surface belongs to the DOMAIN a
    routine names in its own routine.yaml, so `config` on a lane PATCH is an unknown key —
    refused, never quietly applied. One record carrying both axes makes moving a member
    between lanes a silent permissions change — a scheduling decision doing the work of a
    permissions one.
    """
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    body = client.post("/api/lanes",
                       json={"name": "Morning", "members": [{"slug": "alpha"}]}).json()
    assert "config" not in body["lane"]
    r = client.patch(f"/api/lanes/{body['lane']['id']}",
                     json={"config": {"permissions": ["memory"]}})
    assert r.status_code == 422 and "config" in str(r.json()["detail"])


def test_a_stale_member_is_kept_but_never_blocks_an_edit(api_client):
    """F442: routines are deleted out of band, so a lane can name a slug that no longer
    resolves. Only the slugs a caller ADDS are validated. Validating the WHOLE submitted list
    refuses every edit to such a lane — both the routine page and the dashboard send the
    members they are keeping alongside the one they are changing — so joining a lane would
    mean repairing it first."""
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "ghost")
    _mk(tmp_path, "beta")
    lane_id = client.post("/api/lanes",
                          json={"name": "Labs",
                                "members": [{"slug": "alpha"},
                                            {"slug": "ghost"}]}).json()["lane"]["id"]
    shutil.rmtree(tmp_path / "routines" / "ghost")        # deleted out of band

    # beta joins, carrying the members it is keeping — the stale one rides along untouched
    r = client.patch(f"/api/lanes/{lane_id}", json={"members": [{"slug": "alpha"},
                                                               {"slug": "ghost"},
                                                               {"slug": "beta"}]})
    assert r.status_code == 200, r.text
    assert r.json()["lane"]["members"] == [m("alpha"), m("ghost"), m("beta")]

    # a slug that was never a routine is still refused — the exemption is per-lane, not global
    r = client.patch(f"/api/lanes/{lane_id}", json={"members": [{"slug": "alpha"},
                                                               {"slug": "nobody"}]})
    assert r.status_code == 400 and "nobody" in r.json()["detail"]

    # and removing the stale member is an ordinary edit
    r = client.patch(f"/api/lanes/{lane_id}", json={"members": [{"slug": "alpha"},
                                                               {"slug": "beta"}]})
    assert r.status_code == 200 and r.json()["lane"]["members"] == [m("alpha"), m("beta")]


def test_validate_names_a_phantom_member_and_an_empty_scheduled_lane(tmp_path):
    """The two instance-level cases no routine's own setup surface can see. Nothing cascades
    a deletion out of the store, so `rsched validate` is where a phantom member surfaces."""
    from rsched.cli import _instance_problems

    _mk(tmp_path, "alpha")
    home = tmp_path / "routines"
    server = SimpleNamespace(routines_home=home)
    lanes.create(home, name="Labs", members=[m("alpha"), m("ghost")])
    lanes.create(home, name="Empty", members=[], cron="0 7 * * *")

    lines = _instance_problems(server)
    assert any("'ghost'" in ln and "not a routine" in ln for ln in lines)
    assert any("Empty" in ln and "no members" in ln for ln in lines)
    assert not any("alpha" in ln for ln in lines)


def test_api_run_lane_arms_a_chain(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "beta")

    # arming an unknown lane 404s
    assert client.post("/api/lanes/lane-nope/run").status_code == 404

    lane_id = client.post("/api/lanes",
                          json={"name": "Chain",
                                "members": [{"slug": "alpha"},
                                            {"slug": "beta"}]}).json()["lane"]["id"]

    # a memberless lane cannot be fired
    empty = client.post("/api/lanes", json={"name": "Empty"}).json()["lane"]["id"]
    assert client.post(f"/api/lanes/{empty}/run").status_code == 400

    # arm: the chain lands, snapshotting member records + the resolved policy (default
    # 'stop')
    r = client.post(f"/api/lanes/{lane_id}/run")
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["members"] == [m("alpha"), m("beta")]
    assert run["on_failure"] == "stop"
    assert run["cursor"] == 0 and run["status"] == "pending"

    # GET /api/lanes now surfaces the in-flight chain
    body = client.get("/api/lanes").json()
    assert lane_id in body["in_flight"]
    assert body["in_flight"][lane_id]["members"] == [m("alpha"), m("beta")]

    # a second arm of the same lane is a 409 (one chain at a time)
    assert client.post(f"/api/lanes/{lane_id}/run").status_code == 409


# -- the lane schedule (D71) ---------------------------------------------------------------


def test_store_cron_validation_and_clear(tmp_path):
    home = tmp_path
    rec = lanes.create(home, name="Sched", cron="0 7 * * *", tz="UTC")
    assert rec["cron"] == "0 7 * * *" and rec["tz"] == "UTC"
    assert lanes.scheduled_member_slugs(home) == set()      # no members yet
    lanes.update(home, rec["id"], members=[m("alpha")])
    assert lanes.scheduled_member_slugs(home) == {"alpha"}
    # clearing the schedule un-suppresses the members
    lanes.update(home, rec["id"], cron="")
    assert lanes.scheduled_member_slugs(home) == set()
    # bad cron is rejected on create and update
    try:
        lanes.create(home, name="Bad", cron="not a cron")
        raise AssertionError("expected ValueError for bad cron")
    except ValueError:
        pass
    try:
        lanes.update(home, rec["id"], cron="61 * * * *")
        raise AssertionError("expected ValueError for bad cron")
    except ValueError:
        pass
    # a corrupt stored cron degrades to unscheduled instead of raising
    from rsched.paths import atomic_write_json
    atomic_write_json(lanes.lanes_file(home),
                      {"lanes": [{"id": "lane-x", "name": "G", "cron": "junk junk"}]})
    assert lanes.load(home)["lanes"][0]["cron"] == ""


def test_lane_patch_forbids_unknown_keys(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    lane_id = client.post("/api/lanes", json={"name": "G", "members": [{"slug": "alpha"}]}) \
                .json()["lane"]["id"]
    r = client.patch(f"/api/lanes/{lane_id}", json={"membrs": [{"slug": "alpha"}]})
    assert r.status_code == 422 and "membrs" in str(r.json()["detail"])
    assert client.patch(f"/api/lanes/{lane_id}", json={"name": "G2"}).status_code == 200


def test_api_lane_schedule_roundtrip(api_client):
    """D71 web half: PATCH {schedule: {friendly}} → cron + server tz recorded; GET rides
    the friendly prefill back; a manual spec clears the schedule; a bad spec 400s."""
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    lane_id = client.post("/api/lanes",
                          json={"name": "Sched",
                                "members": [{"slug": "alpha"}]}).json()["lane"]["id"]

    r = client.patch(f"/api/lanes/{lane_id}",
                     json={"schedule": {"friendly": {"frequency": "daily", "time": "07:30"}}})
    assert r.status_code == 200, r.text
    assert r.json()["lane"]["cron"] == "30 7 * * *"
    assert r.json()["lane"]["tz"]                      # the server zone rides beside it

    body = client.get("/api/lanes").json()
    lane = next(rec for rec in body["lanes"] if rec["id"] == lane_id)
    assert lane["schedule_friendly"] == {"frequency": "daily", "time": "07:30"}
    assert body["server_tz"]

    # manual clears it
    r = client.patch(f"/api/lanes/{lane_id}",
                     json={"schedule": {"friendly": {"frequency": "manual"}}})
    assert r.status_code == 200
    assert r.json()["lane"]["cron"] == "" and r.json()["lane"]["tz"] == ""

    # a bad friendly spec is a 400; the stored schedule is untouched
    r = client.patch(f"/api/lanes/{lane_id}",
                     json={"schedule": {"friendly": {"frequency": "sometimes"}}})
    assert r.status_code == 400
    assert client.get("/api/lanes").json()["lanes"][0]["cron"] == ""


# -- whole-lane pause ----------------------------------------------------------------------
# Pausing a lane stops its cron from auto-arming the chain while its members STAY
# lane-managed (their own crons remain suppressed), so the whole set goes quiet with one
# switch; an explicit run (UI "Run now" / manage_lane run) still fires. Resuming must
# yield a FUTURE fire, never a backlog of the fires missed while paused.


def test_store_paused_roundtrip(tmp_path):
    home = tmp_path
    rec = lanes.create(home, name="G", cron="0 7 * * *", tz="UTC")
    assert rec["paused"] is False                       # born unpaused
    lanes.update(home, rec["id"], paused=True)
    assert lanes.get(home, rec["id"])["paused"] is True
    lanes.update(home, rec["id"], name="G2")           # untouched fields stay put
    assert lanes.get(home, rec["id"])["paused"] is True
    lanes.update(home, rec["id"], paused=False)
    assert lanes.get(home, rec["id"])["paused"] is False
    # a hand-edited truthy value normalizes to a real bool on load
    from rsched.paths import atomic_write_json
    atomic_write_json(lanes.lanes_file(home),
                      {"lanes": [{"id": "lane-x", "name": "G", "paused": "yes"}]})
    assert lanes.load(home)["lanes"][0]["paused"] is True


def test_paused_lane_leaves_the_daemon_fire_table():
    """The daemon half: a paused lane's schedulable reads as DISABLED, so next_fire is
    None — the cron never auto-arms and nothing needs skipping in the fire loop; resuming
    reads as enabled again and yields the next future fire."""
    from datetime import UTC, datetime

    from rsched import registry
    from rsched.daemon.scheduler import Scheduler
    lane = {"cron": "0 7 * * *", "tz": "UTC", "paused": True}
    now = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    assert registry.next_fire(Scheduler._lane_schedulable(lane), now) is None
    lane["paused"] = False
    nf = registry.next_fire(Scheduler._lane_schedulable(lane), now)
    assert nf is not None and nf.hour == 7


def test_api_lane_pause_toggle(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    lane_id = client.post("/api/lanes",
                          json={"name": "P",
                                "members": [{"slug": "alpha"}]}).json()["lane"]["id"]
    r = client.patch(f"/api/lanes/{lane_id}", json={"paused": True})
    assert r.status_code == 200, r.text
    assert r.json()["lane"]["paused"] is True
    assert client.get("/api/lanes").json()["lanes"][0]["paused"] is True
    r = client.patch(f"/api/lanes/{lane_id}", json={"paused": False})
    assert r.status_code == 200
    assert r.json()["lane"]["paused"] is False
