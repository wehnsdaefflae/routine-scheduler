"""Routine groups: the store (rsched.groups) + its CRUD API (web.api_groups), the shared
group store injected into member runs' fs roots (D67), and the group schedule (D71).

These tests pin the store's shape guarantees (ordered/deduped members, on_failure
vocabulary, the update tri-state), the API's member-existence validation against the live
registry, and the shared-store root injection end to end.
"""

from __future__ import annotations

from pathlib import Path

from rsched import groups

# -- store ---------------------------------------------------------------------------------

def test_store_empty_reads_as_default(tmp_path):
    home = tmp_path
    data = groups.load(home)
    assert data == {"default_on_failure": "stop", "groups": []}
    assert not groups.groups_file(home).exists()   # a pure read never writes


def test_create_preserves_order_and_dedups_members(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="Morning", members=["b", "a", "b", "", "c"])
    assert rec["members"] == ["b", "a", "c"]       # order kept, dupes + blanks dropped
    assert rec["id"].startswith("grp-")
    assert rec["on_failure"] is None               # inherit by default
    assert groups.get(home, rec["id"]) == rec


def test_create_rejects_blank_name_and_bad_on_failure(tmp_path):
    home = tmp_path
    for bad in ("", "   "):
        try:
            groups.create(home, name=bad)
            raise AssertionError("expected ValueError for blank name")
        except ValueError:
            pass
    try:
        groups.create(home, name="ok", on_failure="explode")
        raise AssertionError("expected ValueError for bad on_failure")
    except ValueError:
        pass


def test_update_on_failure_tristate(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="G", on_failure="continue")
    assert rec["on_failure"] == "continue"
    # omit on_failure → unchanged; rename only
    groups.update(home, rec["id"], name="G2")
    assert groups.get(home, rec["id"])["on_failure"] == "continue"
    assert groups.get(home, rec["id"])["name"] == "G2"
    # explicit None → inherit
    groups.update(home, rec["id"], on_failure=None)
    assert groups.get(home, rec["id"])["on_failure"] is None
    # explicit value → override
    groups.update(home, rec["id"], on_failure="stop")
    assert groups.get(home, rec["id"])["on_failure"] == "stop"


def test_set_default_and_delete(tmp_path):
    home = tmp_path
    assert groups.default_on_failure(home) == "stop"
    groups.set_default_on_failure(home, "continue")
    assert groups.default_on_failure(home) == "continue"
    rec = groups.create(home, name="G")
    assert groups.delete(home, rec["id"]) is True
    assert groups.delete(home, rec["id"]) is False   # idempotent
    assert groups.get(home, rec["id"]) is None
    # deleting a group leaves the default intact
    assert groups.default_on_failure(home) == "continue"


def test_load_normalizes_corrupt_shape(tmp_path):
    home = tmp_path
    from rsched.paths import atomic_write_json
    atomic_write_json(groups.groups_file(home),
                      {"default_on_failure": "nonsense",
                       "groups": [{"id": "grp-x", "name": "G", "members": ["a", "a"],
                                   "on_failure": "bad"}, "junk", 42]})
    data = groups.load(home)
    assert data["default_on_failure"] == "stop"            # bad default → built-in
    assert len(data["groups"]) == 1                          # non-dicts dropped
    assert data["groups"][0]["members"] == ["a"]            # deduped
    assert data["groups"][0]["on_failure"] is None          # bad value → inherit


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


def test_api_group_lifecycle(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "beta")

    # empty list carries the default + vocab + the routine picker
    r = client.get("/api/groups")
    assert r.status_code == 200
    body = r.json()
    assert body["default_on_failure"] == "stop"
    assert body["on_failure_vocab"] == ["stop", "continue"]
    assert body["groups"] == []
    assert {k["slug"] for k in body["known_routines"]} == {"alpha", "beta"}

    # create with real members
    r = client.post("/api/groups", json={"name": "Morning", "members": ["beta", "alpha"]})
    assert r.status_code == 200, r.text
    gid = r.json()["group"]["id"]
    assert r.json()["group"]["members"] == ["beta", "alpha"]

    # unknown member is rejected, naming the slug
    r = client.post("/api/groups", json={"name": "Bad", "members": ["ghost"]})
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]

    # patch: reorder + set on_failure override
    r = client.patch(f"/api/groups/{gid}",
                     json={"members": ["alpha", "beta"], "on_failure": "continue",
                           "set_on_failure": True})
    assert r.status_code == 200, r.text
    assert r.json()["group"]["members"] == ["alpha", "beta"]
    assert r.json()["group"]["on_failure"] == "continue"

    # set the instance default
    r = client.put("/api/groups/default", json={"default_on_failure": "continue"})
    assert r.status_code == 200
    assert client.get("/api/groups").json()["default_on_failure"] == "continue"

    # a bad default value is a 400
    assert client.put("/api/groups/default",
                      json={"default_on_failure": "explode"}).status_code == 400

    # delete, then a second delete 404s
    assert client.delete(f"/api/groups/{gid}").status_code == 200
    assert client.delete(f"/api/groups/{gid}").status_code == 404
    assert client.get("/api/groups").json()["groups"] == []


def test_api_run_group_arms_a_chain(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "beta")

    # arming an unknown group 404s
    assert client.post("/api/groups/grp-nope/run").status_code == 404

    gid = client.post("/api/groups",
                      json={"name": "Chain", "members": ["alpha", "beta"]}).json()["group"]["id"]

    # a memberless group cannot be fired
    empty = client.post("/api/groups", json={"name": "Empty"}).json()["group"]["id"]
    assert client.post(f"/api/groups/{empty}/run").status_code == 400

    # arm: the chain lands, snapshotting members + the resolved policy (default 'stop')
    r = client.post(f"/api/groups/{gid}/run")
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["members"] == ["alpha", "beta"] and run["on_failure"] == "stop"
    assert run["cursor"] == 0 and run["status"] == "pending"

    # GET /groups now surfaces the in-flight chain
    body = client.get("/api/groups").json()
    assert gid in body["in_flight"]
    assert body["in_flight"][gid]["members"] == ["alpha", "beta"]

    # a second arm of the same group is a 409 (one chain at a time)
    assert client.post(f"/api/groups/{gid}/run").status_code == 409


# -- the group schedule (D71) --------------------------------------------------------------


def test_store_cron_validation_and_clear(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="Sched", cron="0 7 * * *", tz="UTC")
    assert rec["cron"] == "0 7 * * *" and rec["tz"] == "UTC"
    assert groups.scheduled_member_slugs(home) == set()      # no members yet
    groups.update(home, rec["id"], members=["alpha"])
    assert groups.scheduled_member_slugs(home) == {"alpha"}
    # clearing the schedule un-suppresses the members
    groups.update(home, rec["id"], cron="")
    assert groups.scheduled_member_slugs(home) == set()
    # bad cron is rejected on create and update
    try:
        groups.create(home, name="Bad", cron="not a cron")
        raise AssertionError("expected ValueError for bad cron")
    except ValueError:
        pass
    try:
        groups.update(home, rec["id"], cron="61 * * * *")
        raise AssertionError("expected ValueError for bad cron")
    except ValueError:
        pass
    # a corrupt stored cron degrades to unscheduled instead of raising
    from rsched.paths import atomic_write_json
    atomic_write_json(groups.groups_file(home),
                      {"groups": [{"id": "grp-x", "name": "G", "cron": "junk junk"}]})
    assert groups.load(home)["groups"][0]["cron"] == ""


def test_group_patch_forbids_unknown_keys(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    gid = client.post("/api/groups", json={"name": "G", "members": ["alpha"]}) \
                .json()["group"]["id"]
    r = client.patch(f"/api/groups/{gid}", json={"membrs": ["alpha"]})
    assert r.status_code == 422 and "membrs" in str(r.json()["detail"])
    assert client.patch(f"/api/groups/{gid}", json={"name": "G2"}).status_code == 200


def test_api_group_schedule_roundtrip(api_client):
    """D71 web half: PATCH {schedule: {friendly}} → cron + server tz recorded; GET rides
    the friendly prefill back; a manual spec clears the schedule; a bad spec 400s."""
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    gid = client.post("/api/groups",
                      json={"name": "Sched", "members": ["alpha"]}).json()["group"]["id"]

    r = client.patch(f"/api/groups/{gid}",
                     json={"schedule": {"friendly": {"frequency": "daily", "time": "07:30"}}})
    assert r.status_code == 200, r.text
    assert r.json()["group"]["cron"] == "30 7 * * *"
    assert r.json()["group"]["tz"]                      # the server zone rides beside it

    body = client.get("/api/groups").json()
    grp = next(g for g in body["groups"] if g["id"] == gid)
    assert grp["schedule_friendly"] == {"frequency": "daily", "time": "07:30"}
    assert body["server_tz"]

    # manual clears it
    r = client.patch(f"/api/groups/{gid}",
                     json={"schedule": {"friendly": {"frequency": "manual"}}})
    assert r.status_code == 200
    assert r.json()["group"]["cron"] == "" and r.json()["group"]["tz"] == ""

    # a bad friendly spec is a 400, and the stored schedule is untouched
    r = client.patch(f"/api/groups/{gid}",
                     json={"schedule": {"friendly": {"frequency": "sometimes"}}})
    assert r.status_code == 400
    assert client.get("/api/groups").json()["groups"][0]["cron"] == ""


# -- whole-group pause -----------------------------------------------------------------
# Pausing a group stops its cron from auto-arming the chain while its members STAY
# group-managed (their own crons remain suppressed), so the whole set goes quiet with one
# switch; an explicit run (UI "Run now" / manage_group run) still fires. Resuming must
# yield a FUTURE fire, never a backlog of the fires missed while paused.


def test_store_paused_roundtrip(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="G", cron="0 7 * * *", tz="UTC")
    assert rec["paused"] is False                       # born unpaused
    groups.update(home, rec["id"], paused=True)
    assert groups.get(home, rec["id"])["paused"] is True
    groups.update(home, rec["id"], name="G2")           # untouched fields stay put
    assert groups.get(home, rec["id"])["paused"] is True
    groups.update(home, rec["id"], paused=False)
    assert groups.get(home, rec["id"])["paused"] is False
    # a hand-edited truthy value normalizes to a real bool on load
    from rsched.paths import atomic_write_json
    atomic_write_json(groups.groups_file(home),
                      {"groups": [{"id": "grp-x", "name": "G", "paused": "yes"}]})
    assert groups.load(home)["groups"][0]["paused"] is True


def test_paused_group_leaves_the_daemon_fire_table():
    """The daemon half: a paused group's schedulable reads as DISABLED, so next_fire is
    None — the cron never auto-arms and nothing needs skipping in the fire loop; resuming
    reads as enabled again and yields the next future fire."""
    from datetime import UTC, datetime

    from rsched import registry
    from rsched.daemon.scheduler import Scheduler
    g = {"cron": "0 7 * * *", "tz": "UTC", "paused": True}
    now = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    assert registry.next_fire(Scheduler._group_schedulable(g), now) is None
    g["paused"] = False
    nf = registry.next_fire(Scheduler._group_schedulable(g), now)
    assert nf is not None and nf.hour == 7


def test_api_group_pause_toggle(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    gid = client.post("/api/groups",
                      json={"name": "P", "members": ["alpha"]}).json()["group"]["id"]
    r = client.patch(f"/api/groups/{gid}", json={"paused": True})
    assert r.status_code == 200, r.text
    assert r.json()["group"]["paused"] is True
    assert client.get("/api/groups").json()["groups"][0]["paused"] is True
    r = client.patch(f"/api/groups/{gid}", json={"paused": False})
    assert r.status_code == 200
    assert r.json()["group"]["paused"] is False


# -- the shared group store (D67, option B-i) ----------------------------------------------


def test_member_store_roots_lookup_and_lazy_creation(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="Morning", members=["alpha", "beta"])
    # lookup without create: named but not materialized
    roots = groups.member_store_roots(home, "alpha")
    assert roots == [groups.store_dir(home, rec["id"])]
    assert not roots[0].exists()
    # create=True (the boot seam) materializes it; non-members get nothing
    roots = groups.member_store_roots(home, "beta", create=True)
    assert roots[0].is_dir()
    assert groups.member_store_roots(home, "loner") == []


def test_grouped_run_reads_and_writes_the_shared_store(make_routine, scripted):
    """D67 end to end: a grouped routine's run gets .control/group-stores/<gid>/ as an
    injected fs read+write root — file actions on it succeed with no grant dance — while
    an ungrouped run's roots never carry it."""
    from rsched.engine.runtime import run_routine
    from test_loop import _server

    d = make_routine(slug="grpmember")
    server = _server(d)
    rec = groups.create(server.routines_home, name="Pipeline", members=["grpmember"])
    store = groups.store_dir(server.routines_home, rec["id"])
    scripted([
        {"say": "leave a note for the group", "kind": "write_file",
         "path": str(store / "grpmember-status.md"), "content": "ingest done\n"},
        {"say": "read it back", "kind": "read_file",
         "path": str(store / "grpmember-status.md")},
        {"say": "done", "kind": "finish", "status": "ok",
         "summary": "wrote and re-read the group note, eight words here now yes ok done"},
    ])
    status, run_dir = run_routine(d, server, run_ts="20260805-070000")
    assert status == "ok"
    assert (store / "grpmember-status.md").read_text() == "ingest done\n"
    from rsched.engine.transcript import read_events
    events = read_events(run_dir / "transcript.jsonl")[0]
    reads = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "read_file"]
    assert reads and not reads[0]["payload"].get("error")


def test_ungrouped_run_context_has_no_store_root(tmp_path):
    from types import SimpleNamespace

    from rsched.config import ServerConfig
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    routine = SimpleNamespace(slug="solo", dir=tmp_path / "solo",
                              fs_read_roots=[], fs_write_roots=[])
    (tmp_path / "solo").mkdir()
    ctx = RunContext(routine=routine, server=ServerConfig(), registry=None,
                     run_ts="20260805-070000", run_dir=tmp_path / "solo" / "runs" / "x",
                     transcript=Transcript(tmp_path / "t.jsonl"),
                     budgets=Budgets(max_turns=1, max_wall_clock_min=1,
                                     max_total_tokens=1, max_subruns=1,
                                     max_subrun_depth=1, ask_timeout_min=1))
    assert ctx.group_store_roots == []
    assert ctx.read_roots() == [] and ctx.write_roots() == []
