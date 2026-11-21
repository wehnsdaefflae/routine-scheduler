"""The REVISE path of `create_routine` (D198) — rewriting an EXISTING routine's recipe in
place, behind ONE operator decision.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import seeded_server
from rsched import pending, reciperevise
from rsched.engine.create_routine import handle_create_routine
from rsched.engine.reviseroutine import KIND

ASK = {"target": "vodafone-outage-claims", "name": "x", "revises": True,
       "prompt": "stages/scan.md: read the cursor before the feed — this run wasted two turns"}


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def improver(server, make_routine):
    d = make_routine(slug="routine-improver")
    make_routine(slug="vodafone-outage-claims")
    return SimpleNamespace(server=server, depth=0, run_id="routine-improver:20261031-010000",
                           routine=SimpleNamespace(slug="routine-improver", dir=d,
                                                   capabilities={"actions": ["write_recipe"]}))


def test_no_revision_without_recipe_authoring(improver):
    improver.routine.capabilities = {"actions": []}
    obs = handle_create_routine(improver, ASK)
    assert obs["rejected"] and "write_recipe" in obs["reason"]
    assert pending.load_all(improver.server.routines_home) == []


def test_a_child_run_and_an_unknown_target_are_refused(improver):
    child = SimpleNamespace(**{**improver.__dict__, "depth": 1})
    assert handle_create_routine(child, ASK)["rejected"]
    gone = handle_create_routine(improver, {**ASK, "target": "no-such-routine"})
    assert gone["rejected"] and "not a routine to revise" in gone["reason"]


def test_its_own_recipe_is_not_this_path(improver):
    obs = handle_create_routine(improver, {**ASK, "target": "routine-improver"})
    assert obs["rejected"] and "your OWN recipe" in obs["reason"]


def test_a_revision_files_one_decision_and_writes_nothing(improver, server):
    home = server.routines_home
    obs = handle_create_routine(improver, ASK)
    assert obs["queued"] and not obs.get("armed")
    [rec] = pending.load_all(home)
    assert rec["kind"] == KIND and rec["fields"]["target"] == "vodafone-outage-claims"
    # nothing reached the recipe, and nothing is waiting for the target's next run either
    assert reciperevise.pending(home, "vodafone-outage-claims") == []
    assert "scan.md" not in (home / "vodafone-outage-claims" / "main.md").read_text()
    # one standing card per proposer × target × wording
    handle_create_routine(improver, ASK)
    assert len(pending.load_all(home)) == 1


def test_approving_the_card_hands_the_lines_to_the_target(improver, server):
    from conftest import authed_client

    home = server.routines_home
    handle_create_routine(improver, ASK)
    [rec] = pending.load_all(home)
    with authed_client(server) as c:
        r = c.post(f"/api/pending-creations/{rec['id']}/materialize")
        assert r.status_code == 200, r.text
        assert r.json()["revises"] == "vodafone-outage-claims"
    [waiting] = reciperevise.pending(home, "vodafone-outage-claims")
    assert waiting.is_file() and pending.load_all(home) == []
    # the target's run is told whose words they are, and may refuse a line it finds wrong
    told = reciperevise.instruction(reciperevise.take(home, "vodafone-outage-claims"))
    assert "routine-improver" in told and "NOT YOURS" in told
    assert "do NOT make that edit" in told
    assert "routine.yaml" in told and "sealed" in told
    # one-shot: consumed entries are gone, so the next fire is not handed them again
    assert reciperevise.pending(home, "vodafone-outage-claims") == []
