"""The REVISE path of `create_routine` (D198) — rewriting an EXISTING routine's recipe in
place, gated behind that routine's own `revise_confirm` dial.

The operator's answer, 2026-10-19: *"Add a revise path to `create_routine` (or a sibling
action) gated by a per-routine dial, like `config_confirm`"*. These tests pin the three things
that answer makes true: a caller without recipe-authoring is refused; the dial consulted is the
TARGET's, not the caller's (`configdial`'s cross-routine rule, which this one follows); and the
EDIT is always made by the routine that owns the recipe, never by the proposer — `always` files
one decision card, `invited` hands the prose straight to the target's next run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from conftest import seeded_server
from rsched import pending, reciperevise, revisedial
from rsched.engine.create_routine import handle_create_routine
from rsched.engine.reviseroutine import KIND
from rsched.grants import APPROVAL_DIALS

ASK = {"target": "vodafone-outage-claims", "name": "x", "revises": True,
       "prompt": "stages/scan.md: read the cursor before the feed — this run wasted two turns"}


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


def _dial(routine_dir, level):
    raw = yaml.safe_load((routine_dir / "routine.yaml").read_text())
    raw.setdefault("capabilities", {})["revise_confirm"] = level
    (routine_dir / "routine.yaml").write_text(yaml.safe_dump(raw))


@pytest.fixture
def improver(server, make_routine):
    d = make_routine(slug="routine-improver")
    make_routine(slug="vodafone-outage-claims")
    return SimpleNamespace(server=server, depth=0, run_id="routine-improver:20261031-010000",
                           routine=SimpleNamespace(slug="routine-improver", dir=d,
                                                   capabilities={"actions": ["write_recipe"]}))


def test_the_dial_is_registered_with_its_own_ladder():
    # `config_confirm` is the model the answer names, and its ladder is its own — so is this
    # one: two rungs, because a recipe has no field allow-list to make a middle one from.
    assert APPROVAL_DIALS[revisedial.DIAL] == revisedial.LEVELS == ("always", "invited")
    assert revisedial.level_of({}) == "always"          # omitted reads as today's behaviour
    assert revisedial.level_of({"revise_confirm": "nonsense"}) == "always"   # a typo fails CLOSED
    assert not revisedial.armed_without_a_card({"revise_confirm": "nonsense"})


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


def test_the_default_dial_files_one_decision_and_writes_nothing(improver, server):
    home = server.routines_home
    obs = handle_create_routine(improver, ASK)
    assert obs["queued"] and obs["dial"] == "always" and not obs.get("armed")
    [rec] = pending.load_all(home)
    assert rec["kind"] == KIND and rec["fields"]["target"] == "vodafone-outage-claims"
    # nothing reached the recipe, and nothing is waiting for the target's next run either
    assert reciperevise.pending(home, "vodafone-outage-claims") == []
    assert "scan.md" not in (home / "vodafone-outage-claims" / "main.md").read_text()
    # one standing card per proposer × target × wording
    handle_create_routine(improver, ASK)
    assert len(pending.load_all(home)) == 1


def test_the_targets_own_dial_arms_it_and_its_run_is_told_whose_words_they_are(improver, server):
    home = server.routines_home
    _dial(home / "vodafone-outage-claims", "invited")
    obs = handle_create_routine(improver, ASK)
    assert obs["armed"] and obs["dial"] == "invited" and not obs.get("queued")
    assert pending.load_all(home) == []          # no card: the dial already answered
    [waiting] = reciperevise.pending(home, "vodafone-outage-claims")
    assert waiting.is_file()
    told = reciperevise.instruction(reciperevise.take(home, "vodafone-outage-claims"))
    assert "routine-improver" in told and "NOT YOURS" in told
    assert "do NOT make that edit" in told       # the target may refuse a line it finds wrong
    assert "routine.yaml" in told and "sealed" in told
    # one-shot: consumed entries are gone, so the next fire is not handed them again
    assert reciperevise.pending(home, "vodafone-outage-claims") == []


def test_the_callers_own_dial_cannot_authorize_it(improver, server):
    """The cross-routine rule the named model states (`configdial.refusals(cross_routine=True)`,
    `engine/autoask.py`): no value of the PROPOSER's dial speaks for the other routine.
    """
    home = server.routines_home
    _dial(home / "routine-improver", "invited")   # the caller's own dial, wide open
    obs = handle_create_routine(improver, ASK)
    assert obs["queued"] and obs["dial"] == "always"   # the TARGET's default still decides
    assert reciperevise.pending(home, "vodafone-outage-claims") == []


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
