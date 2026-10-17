"""A REPLACEMENT CANDIDATE (engine/candidate.py) and its one-accept SWAP (web/api_pending.py).

On 2026-10-17 the operator replaced two failing routines by hand, because the routine that
maintains the others could only make small, reversible changes. These tests pin what replaces
the hand: a run holding the routine-rebuild permission creates the candidate at once — with
the old routine's settings and NONE of its secrets, on no schedule, in no lane — and files ONE
decision; accepting it moves the old routine's lane place, schedule and secrets to the
candidate and switches the old one off, and discarding it switches the candidate off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from conftest import authed_client, seeded_server
from rsched import lanes, pending
from rsched.config import load_routine
from rsched.engine import candidate
from rsched.engine.create_routine import handle_create_routine
from rsched.engine.observations import render_observation

ASK = {"target": "freelance-hunter", "name": "Freelance Hunter", "replaces": "freelance-radar",
       "prompt": "0 interviews from 89 applications in 4 months at 43M volume a run"}


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def improver(server, make_routine):
    d = make_routine(slug="routine-improver")
    old = make_routine(slug="freelance-radar")
    raw = yaml.safe_load((old / "routine.yaml").read_text())
    raw["grants"] = {"secret:GMAIL_APP_PASSWORD": True}
    raw["permissions"] = ["outbound-mail"]
    (old / "routine.yaml").write_text(yaml.safe_dump(raw))
    return SimpleNamespace(server=server, depth=0, run_id="routine-improver:20261017-010000",
                           routine=SimpleNamespace(slug="routine-improver", dir=d,
                                                   capabilities={"actions": ["rebuild_routine"]}))


def test_no_candidate_without_the_rebuild_permission(improver):
    improver.routine.capabilities = {"actions": []}
    obs = handle_create_routine(improver, ASK)
    assert obs["rejected"] and "routine-rebuild" in obs["reason"]
    assert not (improver.server.routines_home / "freelance-hunter").exists()


def test_a_candidate_has_the_old_settings_and_none_of_its_secrets(improver):
    home = improver.server.routines_home
    obs = handle_create_routine(improver, ASK)
    assert obs["candidate"] and obs["replaces"] == "freelance-radar"
    text, _ = render_observation(obs)
    assert "replacement candidate 'freelance-hunter'" in text and "swap" in text
    cfg, _ = load_routine(home / "freelance-hunter")
    assert cfg is not None and cfg.enabled and cfg.cron == ""
    assert cfg.permissions == ["outbound-mail"] and cfg.grants == {}
    assert candidate.load(home)["freelance-hunter"]["replaces"] == "freelance-radar"
    [rec] = pending.load_all(home)
    assert rec["kind"] == "replace-routine" and rec["fields"]["candidate"] == "freelance-hunter"
    # one candidate per routine it would replace
    again = handle_create_routine(improver, {**ASK, "target": "freelance-hunter-2"})
    assert again["rejected"] and "standing candidate" in again["reason"]


def test_the_swap_is_one_accept(improver, server):
    home = server.routines_home
    lane = lanes.create(home, name="Professional", members=[{"slug": "freelance-radar"}],
                        cron="30 4 * * *", tz="Europe/Berlin")
    handle_create_routine(improver, ASK)
    [rec] = pending.load_all(home)
    with authed_client(server) as c:
        r = c.post(f"/api/pending-creations/{rec['id']}/materialize")
        assert r.status_code == 200, r.text
    assert lanes.member_slugs(lanes.get(home, lane["id"])) == ["freelance-hunter"]
    new, _ = load_routine(home / "freelance-hunter")
    old, _ = load_routine(home / "freelance-radar")
    assert new.grants == {"secret:GMAIL_APP_PASSWORD": True} and new.cron == "0 7 * * 1"
    assert new.enabled and not old.enabled
    assert candidate.load(home) == {} and pending.load_all(home) == []


def test_keeping_the_old_one_switches_the_candidate_off(improver, server):
    home = server.routines_home
    handle_create_routine(improver, ASK)
    [rec] = pending.load_all(home)
    with authed_client(server) as c:
        r = c.post(f"/api/pending-creations/{rec['id']}/discard", json={"reason": "kept"})
        assert r.status_code == 200, r.text
    assert not load_routine(home / "freelance-hunter")[0].enabled
    assert load_routine(home / "freelance-radar")[0].enabled
    assert candidate.load(home) == {}
