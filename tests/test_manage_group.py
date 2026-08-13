"""The `manage_group` action (D61): registration + schema, the root-conversation gate, member
validation against the live registry, and the full verb lifecycle (create/update/delete/
set-default/run/list) against the real rsched.groups store — including the flat `split`
subset (F292) the handler folds into member records, and `paused` (D77/D80 parity with the
routines page's group surface).

Group management is initiated from a CONVERSATION only — the handler mirrors create_routine's
root-conversation gate, and the engine only surfaces the kind to a root conversation
(loop.allowed_tools injection), so a scheduled routine never sees it. The routines page
manages the same store from the web.
"""

from types import SimpleNamespace

from rsched import groups
from rsched.config import ServerConfig
from rsched.engine import manage_group
from rsched.engine.actions import KINDS, validate_action


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
    return SimpleNamespace(server=server, routine=routine, depth=depth)


def test_manage_group_registered_and_validated():
    assert "manage_group" in KINDS
    # a well-formed create passes
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "create",
                            "name": "Morning"}) == []
    # a bad verb is rejected
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "frobnicate"})
    # per-verb required fields
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "create"})       # no name
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "update"})       # no target
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "run"})          # no target
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "set-default"})  # no policy
    # a foreign field is rejected (additive schema is still closed)
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "list", "path": "x"})


def test_manage_group_rejected_outside_root_conversation(tmp_path):
    server = _server(tmp_path)
    for ctx in (_ctx(server, home="routines_home", slug="weight-coach"),   # a scheduled routine
                _ctx(server, home="conversations_home", depth=1)):         # a within-reply child
        obs = manage_group.handle_manage_group(ctx, {"kind": "manage_group", "verb": "list"})
        assert obs["rejected"] and "conversation" in obs["reason"]


def test_manage_group_create_validates_members(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    # an unknown member is rejected, nothing stored
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "G", "members": ["nope"]})
    assert obs["rejected"] and "nope" in obs["reason"]
    assert groups.list_groups(server.routines_home) == []


def test_manage_group_lifecycle(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    home = server.routines_home

    # create — flat slugs land as member records (split defaults false)
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "Morning",
              "members": ["weight-coach", "news-digest"], "on_failure": "continue"})
    gid = obs["group"]["id"]
    assert obs["group"]["members"] == [{"slug": "weight-coach", "split": False},
                                       {"slug": "news-digest", "split": False}]
    assert obs["group"]["on_failure"] == "continue"

    # list sees it
    obs = manage_group.handle_manage_group(ctx, {"kind": "manage_group", "verb": "list"})
    assert obs["verb"] == "list" and any(g["id"] == gid for g in obs["groups"])

    # update reorders members
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid,
              "members": ["news-digest", "weight-coach"]})
    assert [x["slug"] for x in obs["group"]["members"]] == ["news-digest", "weight-coach"]

    # set-default
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "set-default", "on_failure": "stop"})
    assert obs["default_on_failure"] == "stop"
    assert groups.default_on_failure(home) == "stop"

    # run arms a fire; a second run is refused (one chain at a time)
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "run", "target": gid})
    assert obs["verb"] == "run" and obs["group_id"] == gid
    obs2 = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "run", "target": gid})
    assert obs2["rejected"] and "already running" in obs2["reason"]

    # delete
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "delete", "target": gid})
    assert obs["deleted"] == gid
    assert groups.list_groups(home) == []
    # deleting a gone group is a teaching rejection
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "delete", "target": gid})
    assert obs["rejected"]


def test_manage_group_schedules_a_group(tmp_path):
    """R311/R312: a root conversation sets, changes and clears the GROUP's cron itself —
    the user's scheduling request needs no operator round-trip to /groups. The server tz
    is recorded beside a set cron, exactly as the routines page's group surface writes it."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")

    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "FAU",
              "members": ["weight-coach"], "cron": "0 10 * * *"})
    gid = obs["group"]["id"]
    assert obs["group"]["cron"] == "0 10 * * *" and obs["group"]["tz"]

    # update changes the schedule; an absent cron key leaves it unchanged
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "cron": "30 9 * * 1"})
    assert obs["group"]["cron"] == "30 9 * * 1"
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "name": "FAU jobs"})
    assert obs["group"]["cron"] == "30 9 * * 1"

    # empty string clears it (members fire on their own crons again), tz cleared with it
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "cron": ""})
    assert obs["group"]["cron"] == "" and obs["group"]["tz"] == ""

    # a bad cron is a teaching rejection, not a crash
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "cron": "not a cron"})
    assert obs["rejected"] and "cron" in obs["reason"]
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "Bad", "cron": "nope"})
    assert obs["rejected"] and "cron" in obs["reason"]

    # the schema accepts the field
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "update",
                            "target": gid, "cron": "0 10 * * *"}) == []


def test_manage_group_run_empty_group_rejected(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "Empty"})
    gid = obs["group"]["id"]
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "run", "target": gid})
    assert obs["rejected"] and "no members" in obs["reason"]


def test_manage_group_split_subset(tmp_path):
    """F292: `split` names the members that fire once per two-phase pass. Create takes it
    beside `members`; update semantics — members without split keeps flags, split without
    members re-flags the existing list, a non-member split slug is a teaching rejection."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")

    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "Pipe",
              "members": ["weight-coach", "news-digest"], "split": ["news-digest"]})
    gid = obs["group"]["id"]
    assert obs["group"]["members"] == [{"slug": "weight-coach", "split": False},
                                       {"slug": "news-digest", "split": True}]

    # reorder WITHOUT split → each kept member keeps its flag
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid,
              "members": ["news-digest", "weight-coach"]})
    assert obs["group"]["members"] == [{"slug": "news-digest", "split": True},
                                       {"slug": "weight-coach", "split": False}]

    # split WITHOUT members → re-flags the existing member list
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid,
              "split": ["weight-coach"]})
    assert obs["group"]["members"] == [{"slug": "news-digest", "split": False},
                                       {"slug": "weight-coach", "split": True}]

    # a split slug that is not a member is a teaching rejection, nothing stored
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid,
              "split": ["ghost"]})
    assert obs["rejected"] and "non-member" in obs["reason"]
    assert groups.split_slugs(groups.get(server.routines_home, gid)) == ["weight-coach"]

    # create rejects split ⊄ members too
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "Bad",
              "members": ["weight-coach"], "split": ["news-digest"]})
    assert obs["rejected"] and "non-member" in obs["reason"]

    # the schema accepts the field
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "update",
                            "target": gid, "split": ["weight-coach"]}) == []


def test_manage_group_pause_toggle(tmp_path):
    """D77 parity with the routines page's group surface: update carries `paused` (gate the
    cron, keep it stored); absent = unchanged."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "create", "name": "P",
              "members": ["weight-coach"], "cron": "0 7 * * *"})
    gid = obs["group"]["id"]
    assert obs["group"]["paused"] is False

    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "paused": True})
    assert obs["group"]["paused"] is True and obs["group"]["cron"] == "0 7 * * *"

    # absent key leaves it unchanged; explicit false resumes
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "name": "P2"})
    assert obs["group"]["paused"] is True
    obs = manage_group.handle_manage_group(
        ctx, {"kind": "manage_group", "verb": "update", "target": gid, "paused": False})
    assert obs["group"]["paused"] is False
    assert validate_action({"say": "s", "kind": "manage_group", "verb": "update",
                            "target": gid, "paused": True}) == []
