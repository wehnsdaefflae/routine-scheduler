"""Queued creation from a scheduled run (F328).

R353 is the case these exist for: routine-improver reached a run holding a fully designed,
user-approved routine plus the group it belonged in, and could not materialize either, so the
design was hand-carried back to the operator to paste in. The restriction to conversations was
right — a scheduled run has no user to design WITH — and the missing piece was a QUEUE.

Two invariants: the engine still never writes `routine.yaml` (a run only ever leaves a proposal
under `.control/pending-creations/`), and the proposing routine learns the outcome the ordinary
way, from a message its next run drains.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched import pending
from rsched.engine.create_routine import handle_create_routine
from rsched.engine.manage_group import handle_manage_group
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"
TOKEN = "test-token"


@pytest.fixture
def server(tmp_path):
    lib = tmp_path / "library"
    shutil.copytree(SEED / "workflows", lib / "workflows")
    shutil.copytree(SEED / "rules", lib / "rules")
    shutil.copytree(SEED / "permissions", lib / "permissions")
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            libraries_home=str(lib))


@pytest.fixture
def sched_ctx(server, make_routine):
    """A ctx that looks like a SCHEDULED run: depth 0, but its dir is under routines_home, so
    `_is_root_conversation` is False — exactly the caller that used to be refused."""
    from types import SimpleNamespace

    # make_routine already writes into routines_home — this IS the scheduled routine's dir
    d = make_routine(slug="routine-improver")
    assert d.parent == server.routines_home
    return SimpleNamespace(server=server, depth=0,
                           routine=SimpleNamespace(slug="routine-improver", dir=d),
                           run_id="routine-improver:20260827-030000")


def test_scheduled_run_queues_a_routine_instead_of_being_refused(server, sched_ctx):
    obs = handle_create_routine(sched_ctx, {
        "target": "fau-comms-steward", "name": "FAU comms steward",
        "prompt": "Watch the comms inbox and stage replies.", "workflow": "general-task"})
    assert obs["queued"] is True and not obs.get("rejected")
    # nothing was created — the whole point
    assert not (server.routines_home / "fau-comms-steward").exists()
    recs = pending.load_all(server.routines_home)
    assert len(recs) == 1
    assert recs[0]["kind"] == "create_routine" and recs[0]["routine"] == "routine-improver"
    assert recs[0]["fields"]["instruction"].startswith("Watch the comms inbox")
    # and the run is told plainly not to retry, or it queues a second proposal every run
    assert "Do NOT re-issue" in obs["next"]


def test_scheduled_run_queues_a_group_change_but_still_reads_the_store(server, sched_ctx):
    """`list` writes nothing, and a run that cannot read the group store cannot propose a
    correct change to it — so only the MUTATING verbs queue."""
    listed = handle_manage_group(sched_ctx, {"verb": "list"})
    assert listed["verb"] == "list" and "groups" in listed and not listed.get("queued")

    obs = handle_manage_group(sched_ctx, {
        "verb": "create", "name": "FAU comms", "members": ["routine-improver"]})
    assert obs["queued"] is True
    rec = pending.load_all(server.routines_home)[0]
    assert rec["kind"] == "manage_group" and rec["fields"]["verb"] == "create"
    assert rec["fields"]["members"] == ["routine-improver"]


def test_a_conversation_still_materializes_rather_than_queuing(server, tmp_path):
    """The conversation path is untouched: F328 removed the REFUSAL, not the two-step flow."""
    from types import SimpleNamespace

    from rsched import conversations as conv_mod

    d = conv_mod.create_conversation(server, slug="c-x", first_message="make me a routine")
    ctx = SimpleNamespace(server=server, routine=SimpleNamespace(slug="c-x", dir=d), depth=0,
                          run_id="c-x:1")
    obs = handle_create_routine(ctx, {"target": "newr", "name": "New", "prompt": "do it",
                                      "workflow": "general-task"})
    assert obs.get("draft") is True and not obs.get("queued")
    assert pending.load_all(server.routines_home) == []


def test_notify_proposer_survives_a_routine_that_is_gone(server, sched_ctx):
    """A proposal can outlive its author. That is not an error — it just has nobody to tell."""
    handle_create_routine(sched_ctx, {"target": "x1", "name": "X", "prompt": "p",
                                      "workflow": "general-task"})
    rec = pending.load_all(server.routines_home)[0]
    assert pending.notify_proposer(server, rec, "approved") is True
    shutil.rmtree(server.routines_home / "routine-improver")
    assert pending.notify_proposer(server, rec, "approved") is False


# ---- the web half: the ONLY thing that writes config ----------------------------------------

@pytest.fixture
def client(server):
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_materialize_creates_the_routine_and_tells_the_proposer(server, sched_ctx, client):
    handle_create_routine(sched_ctx, {"target": "fau-comms-steward", "name": "FAU comms steward",
                                      "prompt": "Watch the inbox.", "workflow": "general-task"})
    pid = client.get("/api/pending-creations").json()[0]["id"]

    r = client.post(f"/api/pending-creations/{pid}/materialize")
    assert r.status_code == 200 and r.json()["slug"] == "fau-comms-steward"
    made = server.routines_home / "fau-comms-steward"
    assert (made / "routine.yaml").is_file() and (made / "main.md").is_file()
    # the proposal is consumed, and the proposing routine learns the outcome the ordinary way
    assert client.get("/api/pending-creations").json() == []
    msgs = list((server.routines_home / "routine-improver" / "inbox").glob("msg-pending-*.json"))
    assert len(msgs) == 1
    assert "approved and materialized" in json.loads(msgs[0].read_text())["text"]


def test_materialize_a_group_uses_the_same_store_the_page_writes(server, sched_ctx, client):
    from rsched import groups

    handle_manage_group(sched_ctx, {"verb": "create", "name": "FAU comms",
                                    "members": ["routine-improver"]})
    pid = client.get("/api/pending-creations").json()[0]["id"]
    r = client.post(f"/api/pending-creations/{pid}/materialize")
    assert r.status_code == 200
    made = groups.list_groups(server.routines_home)
    assert len(made) == 1 and made[0]["name"] == "FAU comms"
    assert groups.member_slugs(made[0]) == ["routine-improver"]


def test_discard_removes_it_and_tells_the_proposer_why(server, sched_ctx, client):
    handle_create_routine(sched_ctx, {"target": "nope", "name": "Nope", "prompt": "p",
                                      "workflow": "general-task"})
    pid = client.get("/api/pending-creations").json()[0]["id"]
    r = client.post(f"/api/pending-creations/{pid}/discard",
                    json={"reason": "we already have one"})
    assert r.status_code == 200 and r.json()["notified"] is True
    assert not (server.routines_home / "nope").exists()
    assert client.get("/api/pending-creations").json() == []
    msg = next((server.routines_home / "routine-improver" / "inbox").glob("msg-pending-*.json"))
    assert "discarded (we already have one)" in json.loads(msg.read_text())["text"]


def test_materialize_a_slug_that_appeared_meanwhile_is_a_legible_conflict(server, sched_ctx,
                                                                          client, make_routine):
    """A proposal can sit for days. If the operator made the routine by hand in the meantime,
    the click must say so — not half-build over it."""
    handle_create_routine(sched_ctx, {"target": "taken", "name": "T", "prompt": "p",
                                      "workflow": "general-task"})
    pid = client.get("/api/pending-creations").json()[0]["id"]
    make_routine(slug="taken")          # the operator built it by hand meanwhile
    r = client.post(f"/api/pending-creations/{pid}/materialize")
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert client.get("/api/pending-creations").json()      # still there to discard deliberately


def test_missing_proposal_is_a_404_not_a_crash(client):
    assert client.post("/api/pending-creations/pc-nope/materialize").status_code == 404
    assert client.post("/api/pending-creations/pc-nope/discard", json={}).status_code == 404


def test_the_engine_never_writes_routine_yaml_for_a_queued_creation(server, sched_ctx):
    """The invariant the whole design hangs on: a run leaves a PROPOSAL, and only the web layer
    turns it into config — exactly as it already applies forever-grants."""
    before = set(server.routines_home.rglob("routine.yaml"))
    handle_create_routine(sched_ctx, {"target": "ghost", "name": "G", "prompt": "p",
                                      "workflow": "general-task"})
    handle_manage_group(sched_ctx, {"verb": "create", "name": "G", "members": []})
    assert set(server.routines_home.rglob("routine.yaml")) == before
    assert not (server.routines_home / ".control" / "groups.json").exists()
    # ...and what IS written is only the two proposals
    assert len(pending.load_all(server.routines_home)) == 2


def test_queued_records_carry_the_provenance_the_page_shows(server, sched_ctx):
    handle_create_routine(sched_ctx, {"target": "prov", "name": "P", "prompt": "p",
                                      "workflow": "general-task"})
    rec = pending.load_all(server.routines_home)[0]
    assert rec["routine"] == "routine-improver"
    assert rec["run_id"] == "routine-improver:20260827-030000"
    assert rec["created_at"] and rec["id"].startswith("pc-")
    assert "prov" in rec["summary"] and "general-task" in rec["summary"]


def test_group_update_leaves_unproposed_fields_alone(server, sched_ctx, client):
    """`on_failure` is a tri-state in the store and `cron` "" clears a schedule, so a field the
    proposal never carried must not be rewritten by a default."""
    from rsched import groups

    rec = groups.create(server.routines_home, name="G", members=[{"slug": "routine-improver"}],
                        on_failure="continue", cron="0 7 * * *")
    handle_manage_group(sched_ctx, {"verb": "update", "target": rec["id"], "name": "G2"})
    pid = client.get("/api/pending-creations").json()[0]["id"]
    assert client.post(f"/api/pending-creations/{pid}/materialize").status_code == 200
    after = groups.get(server.routines_home, rec["id"])
    assert after["name"] == "G2"
    assert after["on_failure"] == "continue"          # untouched
    assert yaml.safe_load(json.dumps(after))["cron"] == "0 7 * * *"


def test_a_queued_proposal_never_renders_as_a_completed_action(server, sched_ctx):
    """The observation a queued proposal RENDERS to is the half F328 forgot. Both kinds fell
    through to their success wording over a payload that was not there, so the run was told it
    had created a routine it had not (`created routine 'x' from workflow None`), armed a fire of
    `group None (0 member(s))` (R1200), or emptied a group (`group None (None) now has members
    []`, R1183). Every queued shape must say QUEUED, name the proposal, and name nothing it did
    not do.
    """
    from rsched.engine.obs_admin import format_admin
    from rsched.groups import create as create_group

    grp = create_group(server.routines_home, name="Professional · Daily",
                       members=[{"slug": "routine-improver"}])

    created = handle_create_routine(sched_ctx, {
        "target": "fau-comms-steward", "name": "FAU comms steward",
        "prompt": "Watch the comms inbox.", "workflow": "general-task"})
    line = format_admin(created, "create_routine")
    assert "QUEUED" in line and "NOTHING CHANGED" in line
    assert "fau-comms-steward" in line and "general-task" in line
    assert "created routine" not in line          # the false success it used to render

    for action, must_contain in (
        ({"verb": "update", "target": grp["id"], "members": ["routine-improver"]},
         ("update", "Professional · Daily", "members → ['routine-improver']")),
        ({"verb": "delete", "target": grp["id"]}, ("delete", "Professional · Daily")),
    ):
        obs = handle_manage_group(sched_ctx, action)
        line = format_admin(obs, "manage_group")
        assert obs["queued"] is True
        assert "QUEUED" in line and "NOTHING CHANGED" in line
        assert "group None" not in line and "0 member(s)" not in line
        for fragment in must_contain:
            assert fragment in line, (fragment, line)
        # the operator's Decisions row says the same thing the run was told
        rec = next(r for r in pending.load_all(server.routines_home) if r["id"] == obs["id"])
        assert rec["summary"] == obs["proposal"]

    # `run` is NOT proposable — a fire is ephemeral and the materializer cannot build it, so a
    # no-user run FAILS LOUDLY instead of queuing a dead "create it" card (2026-09-03 fix).
    run_obs = handle_manage_group(sched_ctx, {"verb": "run", "target": grp["id"]})
    assert run_obs.get("rejected") and not run_obs.get("queued")
    assert "cannot be queued" in run_obs["reason"]


def test_a_queued_proposal_naming_an_unknown_group_says_so(server, sched_ctx):
    """A proposal is filed without validating the target (the review is the gate), so the ack
    must not imply the group exists — that is how `group None` read as a real group."""
    from rsched.engine.obs_admin import format_admin

    obs = handle_manage_group(sched_ctx, {"verb": "delete", "target": "grp-nope"})
    line = format_admin(obs, "manage_group")
    assert "unknown group 'grp-nope'" in line and "will fail review" in line
