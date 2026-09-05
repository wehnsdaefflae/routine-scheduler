"""The `manage_lane` action (D61): registration + schema, member validation against the live
registry, the LANE cron verbs (D71/R312), and the full verb lifecycle (create/update/delete/
set-default/run/list) against the real rsched.lanes store — including the flat ordered slugs
the handler folds into member records, `paused` (D77/D80 parity with the routines page's lane
surface), and the members a `list` names in fire order (F424).

The kind covers the TEMPORAL axis alone. A DOMAIN is a per-routine setting living in that
routine's own routine.yaml, which no run writes, so there is no domain verb here to test.

Where a change LANDS depends on who is in the loop (F328). A root conversation applies it
outright; any other depth-0 run queues a mutating verb as a proposal for the Decisions page,
answers `list` directly and is refused a `run` — an approval hours later would fire a stale
chain. A within-reply child is refused outright. The routines page manages the same store from
the web.
"""

from types import SimpleNamespace

from rsched import lanes
from rsched.config import ServerConfig
from rsched.engine import manage_lane
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import KINDS


def _server(tmp_path, members=("weight-coach", "news-digest")):
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir()
    (s.routines_home / ".control").mkdir()
    s.conversations_home = tmp_path / "conversations"
    s.conversations_home.mkdir()
    # Seed real member routine dirs so registry.scan catalogs them (a dir with a routine.yaml
    # is a routine; an unloadable one still catalogs under its dir name).
    for slug in members:
        d = s.routines_home / slug
        d.mkdir()
        (d / "routine.yaml").write_text(f"slug: {slug}\nname: {slug}\n", encoding="utf-8")
    return s


def _ctx(server, *, home: str, slug="c-1", depth=0):
    routine = SimpleNamespace(slug=slug, dir=getattr(server, home) / slug)
    routine.dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(server=server, routine=routine, depth=depth,
                           run_id=f"{slug}:20260827-030000")


def test_manage_lane_registered_and_validated():
    assert "manage_lane" in KINDS
    # a well-formed create passes
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "create",
                            "name": "Morning"}) == []
    # a bad verb is rejected
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "frobnicate"})
    # per-verb required fields
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "create"})       # no name
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "update"})       # no target
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "run"})          # no target
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "set-default"})  # no policy
    # a foreign field is rejected (additive schema is still closed)
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "list", "path": "x"})


def test_manage_lane_queues_changes_outside_a_root_conversation(tmp_path):
    """F328: outside a root conversation a MUTATING verb becomes a proposal for the Decisions
    page; `list` answers directly, because it writes nothing and a run that cannot read the
    store cannot propose a correct change to it."""
    from rsched import pending

    server = _server(tmp_path)
    sched = _ctx(server, home="routines_home", slug="weight-coach")
    child = _ctx(server, home="conversations_home", depth=1)
    for ctx in (sched, child):
        listed = manage_lane.handle_manage_lane(ctx, {"kind": "manage_lane", "verb": "list"})
        assert "lanes" in listed and not listed.get("queued")

    obs = manage_lane.handle_manage_lane(
        sched, {"kind": "manage_lane", "verb": "create", "name": "G"})
    assert obs["queued"] and not obs.get("rejected")
    # a within-reply CHILD is refused outright — it must not reshape lanes as a side effect
    kid = manage_lane.handle_manage_lane(
        child, {"kind": "manage_lane", "verb": "create", "name": "G"})
    assert kid["rejected"] and "child run" in kid["reason"]

    assert lanes.list_lanes(server.routines_home) == []       # nothing applied
    assert len(pending.load_all(server.routines_home)) == 1


def test_manage_lane_run_fails_loudly_with_no_user(tmp_path):
    """A `run` fires an EPHEMERAL lane chain: it writes no config and the materializer cannot
    build it, so a no-user run FAILS LOUDLY rather than queuing a dead 'create it' card
    (R1200). Config verbs (create/update/delete/set-default) queue; `run` does not — an
    approval hours later would fire a stale chain."""
    from rsched import pending

    server = _server(tmp_path)
    lane = lanes.create(server.routines_home, name="G", members=[{"slug": "weight-coach"}])
    sched = _ctx(server, home="routines_home", slug="weight-coach")

    obs = manage_lane.handle_manage_lane(
        sched, {"kind": "manage_lane", "verb": "run", "target": lane["id"]})
    assert obs["rejected"] and not obs.get("queued")
    assert "cannot be queued" in obs["reason"]
    assert pending.load_all(server.routines_home) == []          # nothing queued


def test_manage_lane_create_validates_members(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    # an unknown member is rejected, nothing stored
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "G", "members": ["nope"]})
    assert obs["rejected"] and "nope" in obs["reason"]
    assert lanes.list_lanes(server.routines_home) == []


def test_manage_lane_lifecycle(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    home = server.routines_home

    # create — flat slugs land as member records
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "Morning",
              "members": ["weight-coach", "news-digest"], "on_failure": "continue"})
    lane_id = obs["lane"]["id"]
    assert obs["lane"]["members"] == [{"slug": "weight-coach"},
                                      {"slug": "news-digest"}]
    assert obs["lane"]["on_failure"] == "continue"

    # list sees it
    obs = manage_lane.handle_manage_lane(ctx, {"kind": "manage_lane", "verb": "list"})
    assert obs["verb"] == "list" and any(x["id"] == lane_id for x in obs["lanes"])

    # update reorders members
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id,
              "members": ["news-digest", "weight-coach"]})
    assert [x["slug"] for x in obs["lane"]["members"]] == ["news-digest", "weight-coach"]

    # set-default
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "set-default", "on_failure": "stop"})
    assert obs["default_on_failure"] == "stop"
    assert lanes.default_on_failure(home) == "stop"

    # run arms a fire; a second run is refused (one chain at a time)
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "run", "target": lane_id})
    assert obs["verb"] == "run" and obs["lane_id"] == lane_id
    obs2 = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "run", "target": lane_id})
    assert obs2["rejected"] and "already running" in obs2["reason"]

    # delete
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "delete", "target": lane_id})
    assert obs["deleted"] == lane_id
    assert lanes.list_lanes(home) == []
    # deleting a gone lane is a teaching rejection
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "delete", "target": lane_id})
    assert obs["rejected"]


def test_manage_lane_schedules_a_lane(tmp_path):
    """R311/R312: a root conversation sets, changes and clears the LANE's cron itself — the
    user's scheduling request is completed in the conversation, with no operator round-trip to
    the console. The server tz is recorded beside a set cron, exactly as the web layer's lane
    surface writes it."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")

    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "FAU",
              "members": ["weight-coach"], "cron": "0 10 * * *"})
    lane_id = obs["lane"]["id"]
    assert obs["lane"]["cron"] == "0 10 * * *" and obs["lane"]["tz"]

    # update changes the schedule; an absent cron key leaves it unchanged
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "cron": "30 9 * * 1"})
    assert obs["lane"]["cron"] == "30 9 * * 1"
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "name": "FAU jobs"})
    assert obs["lane"]["cron"] == "30 9 * * 1"

    # empty string clears it (members fire on their own crons again), tz cleared with it
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "cron": ""})
    assert obs["lane"]["cron"] == "" and obs["lane"]["tz"] == ""

    # a bad cron is a teaching rejection, not a crash
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "cron": "not a cron"})
    assert obs["rejected"] and "cron" in obs["reason"]
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "Bad", "cron": "nope"})
    assert obs["rejected"] and "cron" in obs["reason"]

    # the schema accepts the field
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "update",
                            "target": lane_id, "cron": "0 10 * * *"}) == []


def test_manage_lane_run_empty_lane_rejected(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "Empty"})
    lane_id = obs["lane"]["id"]
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "run", "target": lane_id})
    assert obs["rejected"] and "no members" in obs["reason"]


def test_manage_lane_pause_toggle(tmp_path):
    """D77 parity with the web layer's lane surface: update carries `paused` (gate the cron,
    keep it stored); absent = unchanged."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "P",
              "members": ["weight-coach"], "cron": "0 7 * * *"})
    lane_id = obs["lane"]["id"]
    assert obs["lane"]["paused"] is False

    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "paused": True})
    assert obs["lane"]["paused"] is True and obs["lane"]["cron"] == "0 7 * * *"

    # absent key leaves it unchanged; explicit false resumes
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "name": "P2"})
    assert obs["lane"]["paused"] is True
    obs = manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "update", "target": lane_id, "paused": False})
    assert obs["lane"]["paused"] is False
    assert validate_action({"say": "s", "kind": "manage_lane", "verb": "update",
                            "target": lane_id, "paused": True}) == []


def test_manage_lane_list_names_its_members(tmp_path):
    """F424/R1142: the listing answers WHICH routines are in a lane, in fire order — a count
    answers "how big" and nothing else does. A lane's members are its whole semantics, so a
    run reasoning about one is guessing until it is told them."""
    from rsched.engine.obs_admin import format_admin

    server = _server(tmp_path, members=("weight-coach", "tv-tracker"))
    ctx = _ctx(server, home="conversations_home")
    manage_lane.handle_manage_lane(
        ctx, {"kind": "manage_lane", "verb": "create", "name": "Professional · Daily",
              "members": ["weight-coach", "tv-tracker"], "cron": "0 6 * * *"})
    obs = manage_lane.handle_manage_lane(ctx, {"kind": "manage_lane", "verb": "list"})
    line = format_admin(obs, "manage_lane")
    assert "'Professional · Daily'" in line
    assert "weight-coach → tv-tracker" in line     # fire order, not a count
    assert "cron '0 6 * * *'" in line
