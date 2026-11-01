"""A queued proposal's field values are judged at FILING, not on the operator's click (D229/F610).

F610 measured it: "proposed settings carry invalid vocabulary and fail only when the operator
clicks accept — three different fields in one day". The accept is the worst possible place for
that refusal. It reaches the one party who cannot fix the field, at the one moment he cannot act
on it, and the run that COULD have fixed it finished days earlier — so the card sits on the
Decisions page as something he can read and not take.

D229 (operator's answer (a)): validate every proposed value against the LIVE vocabulary at
filing time and refuse the proposal with the field named. These tests are that predicate, from
both ends: the refusal arrives in the observation the FILING run reads, and the Decisions page
shows no proposal whose accept would refuse it.

Each case below is a value that reached the queue BEFORE this change and raised on the accept:
a workflow the library does not hold, a slug `scaffold` refuses, a cron croniter cannot parse,
an `on_failure` outside `lanes.ON_FAILURE`, a lane id the accept cannot find — and one that did
NOT raise and was worse for it: a lane member naming no routine, which the queue path alone
would have built into the store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import authed_client, seeded_server
from helpers import PRODUCES
from rsched import pending, pendingvocab
from rsched.engine.create_routine import handle_create_routine
from rsched.engine.manage_lane import handle_manage_lane


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def sched_ctx(server, make_routine):
    """A ctx that looks like a SCHEDULED run: depth 0, dir under routines_home, so
    `runkind.is_root_conversation` is False — the caller whose proposals go to the queue."""
    d = make_routine(slug="routine-improver")
    return SimpleNamespace(server=server, depth=0,
                           routine=SimpleNamespace(slug="routine-improver", dir=d),
                           run_id="routine-improver:20261101-030000")


@pytest.fixture
def client(server):
    with authed_client(server) as c:
        yield c


def _create(ctx, action: dict) -> dict:
    return handle_create_routine(ctx, {"produces": PRODUCES, **action})


def _queued(server) -> list[dict]:
    return pending.load_all(server.routines_home)


# ---- create_routine --------------------------------------------------------------------------

def test_a_slug_the_scaffold_would_refuse_is_refused_at_filing_with_the_field_named(
        server, sched_ctx):
    """`workflows.scaffold` raises "slug … is not kebab-case" — on the ACCEPT CLICK, because
    nothing judged the slug when the record was written."""
    obs = _create(sched_ctx, {"target": "FAU Comms Steward", "name": "FAU comms steward",
                              "prompt": "Watch the comms inbox.", "workflow": "general-task"})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "slug:" in obs["reason"] and "kebab-case" in obs["reason"]
    assert _queued(server) == []        # the operator never sees a card he cannot accept


def test_the_conversation_only_generate_pseudo_pattern_is_refused_at_filing(server, sched_ctx):
    """`generate` is on the creation CATALOG (it drafts a new pattern while a user watches), so
    the handler's own catalog check passes it — and the Decisions page has no way to draft a
    pattern, so the accept hands `generate` to `scaffold` and gets "workflow 'generate' not
    found in the library". The one value that slipped through BOTH existing checks."""
    obs = _create(sched_ctx, {"target": "fau-comms-steward", "name": "FAU comms steward",
                              "prompt": "Watch the comms inbox.", "workflow": "generate"})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "workflow:" in obs["reason"] and "generate" in obs["reason"]
    assert _queued(server) == []


def test_a_sound_creation_proposal_still_queues_and_still_materializes(server, sched_ctx,
                                                                       client):
    """The gate is at the FRONT: what was proposable stays proposable, and the accept path is
    untouched."""
    obs = _create(sched_ctx, {"target": "fau-comms-steward", "name": "FAU comms steward",
                              "prompt": "Watch the comms inbox.", "workflow": "general-task"})
    assert obs["queued"] is True and not obs.get("rejected")
    pid = client.get("/api/pending-creations").json()[0]["id"]
    assert client.post(f"/api/pending-creations/{pid}/materialize").status_code == 200
    assert (server.routines_home / "fau-comms-steward" / "routine.yaml").is_file()


# ---- manage_lane -----------------------------------------------------------------------------

def test_a_lane_member_naming_no_routine_is_refused_at_filing(server, sched_ctx):
    """The worst of the set, because the accept does NOT refuse it: `api_lanes` and
    `manage_lane` both demand that a member exist, but `api_pending._materialize_lane` passes
    members straight to the store — so the queue path alone built a lane naming a ghost."""
    obs = handle_manage_lane(sched_ctx, {"verb": "create", "name": "FAU comms",
                                         "members": ["routine-improver", "no-such-routine"]})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "members:" in obs["reason"] and "no-such-routine" in obs["reason"]
    assert _queued(server) == []


def test_a_cron_croniter_cannot_parse_is_refused_at_filing(server, sched_ctx):
    obs = handle_manage_lane(sched_ctx, {"verb": "create", "name": "Nightly",
                                         "members": ["routine-improver"],
                                         "cron": "every night at 3"})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "cron:" in obs["reason"] and "every night at 3" in obs["reason"]
    assert _queued(server) == []


def test_an_on_failure_outside_the_stores_own_vocabulary_is_refused_at_filing(server,
                                                                              sched_ctx):
    obs = handle_manage_lane(sched_ctx, {"verb": "create", "name": "FAU comms",
                                         "members": ["routine-improver"],
                                         "on_failure": "halt"})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "on_failure:" in obs["reason"] and "halt" in obs["reason"]
    assert "stop" in obs["reason"] and "continue" in obs["reason"]
    assert _queued(server) == []


def test_a_lane_update_with_no_target_is_refused_at_filing_not_404d_on_the_click(server,
                                                                                 sched_ctx):
    obs = handle_manage_lane(sched_ctx, {"verb": "update", "members": ["routine-improver"]})
    assert obs["rejected"] is True and not obs.get("queued")
    assert "target:" in obs["reason"]
    assert _queued(server) == []


def test_a_sound_lane_proposal_still_queues_and_still_materializes(server, sched_ctx, client):
    from rsched import lanes

    obs = handle_manage_lane(sched_ctx, {"verb": "create", "name": "FAU comms",
                                         "members": ["routine-improver"], "cron": "0 3 * * *",
                                         "on_failure": "continue"})
    assert obs["queued"] is True and not obs.get("rejected")
    pid = client.get("/api/pending-creations").json()[0]["id"]
    assert client.post(f"/api/pending-creations/{pid}/materialize").status_code == 200
    [lane] = lanes.list_lanes(server.routines_home)
    assert lanes.member_slugs(lane) == ["routine-improver"] and lane["cron"] == "0 3 * * *"


# ---- the page's own half, and the records already on disk ------------------------------------

def test_the_decisions_page_shows_no_proposal_whose_accept_would_refuse_it(server, sched_ctx,
                                                                           client):
    """The operator's proof predicate, from his end: file every bad value this gate knows about
    and one good one, then read the page — one card, and it accepts."""
    _create(sched_ctx, {"target": "Not A Slug", "name": "N", "prompt": "p",
                        "workflow": "general-task"})
    _create(sched_ctx, {"target": "fau-comms-steward", "name": "F", "prompt": "p",
                        "workflow": "generate"})
    handle_manage_lane(sched_ctx, {"verb": "create", "name": "Ghost",
                                   "members": ["no-such-routine"]})
    handle_manage_lane(sched_ctx, {"verb": "create", "name": "Bad cron",
                                   "members": ["routine-improver"], "cron": "nightly"})
    _create(sched_ctx, {"target": "fau-comms-steward", "name": "FAU comms steward",
                        "prompt": "Watch the comms inbox.", "workflow": "general-task"})

    cards = client.get("/api/pending-creations").json()
    assert len(cards) == 1
    assert client.post(f"/api/pending-creations/{cards[0]['id']}/materialize").status_code == 200


def test_a_record_queued_before_this_change_still_materializes(server, sched_ctx, client):
    """An already-queued record must not become unacceptable. A record written straight to the
    queue is exactly what one filed before D229 looks like — the gate is at the filing seam, so
    the accept path reads it exactly as it did yesterday."""
    pending.queue(server.routines_home, kind="create_routine", routine="routine-improver",
                  run_id="old-run", summary="routine 'legacy-one' from pattern 'general-task'",
                  fields={"slug": "legacy-one", "name": "Legacy one", "instruction": "do it",
                          "workflow": "general-task", "pattern": "", "setup": [],
                          "done_when": [], "finish_line": [], "never": [],
                          "produces": PRODUCES})
    pid = client.get("/api/pending-creations").json()[0]["id"]
    assert client.post(f"/api/pending-creations/{pid}/materialize").status_code == 200
    assert (server.routines_home / "legacy-one" / "routine.yaml").is_file()


def test_the_judge_reads_the_live_vocabulary_not_a_copy_of_it(server, make_routine):
    """A second hardcoded list would be the defect in a new place. The member check answers to
    the REGISTRY: a slug it refuses today passes the moment that routine exists."""
    bad = {"verb": "create", "name": "L", "members": ["later-routine"]}
    assert "later-routine" in pendingvocab.problem(server, "manage_lane", bad)
    make_routine(slug="later-routine")
    assert pendingvocab.problem(server, "manage_lane", bad) == ""


def test_the_refusal_reaches_the_model_as_prose_naming_the_field(server, sched_ctx):
    """The refusal is only useful if the FILING RUN reads it. F328's own lesson was that
    teaching the handlers and not the renderers makes an observation lie, so the rendered line
    — the model's whole view of this turn — is asserted, not just the dict."""
    from rsched.engine.obs_admin import format_admin

    obs = handle_manage_lane(sched_ctx, {"verb": "create", "name": "Nightly",
                                         "members": ["routine-improver"], "cron": "nightly"})
    line = format_admin(obs, "manage_lane")
    assert line.startswith("OBSERVATION (manage_lane REJECTED)")
    assert "cron:" in line and "nightly" in line
    # and it must not read as a filed proposal: `queued` is False, so the shared queued
    # branch (obs_admin.QUEUEABLE_KINDS) must not claim an id that does not exist
    assert "QUEUED as proposal" not in line

    bad_slug = _create(sched_ctx, {"target": "Not A Slug", "name": "N", "prompt": "p",
                                   "workflow": "general-task"})
    routine_line = format_admin(bad_slug, "create_routine")
    assert routine_line.startswith("OBSERVATION (create_routine REJECTED)")
    assert "slug:" in routine_line


def test_a_queue_kind_with_no_fixed_vocabulary_is_left_alone(server):
    """The gate covers the two kinds that carry vocabulary. `library-drift` and the rest carry
    report ids and prose — judging them here would refuse proposals that work."""
    assert pendingvocab.problem(server, "library-drift", {"entity": "rule:anything"}) == ""
