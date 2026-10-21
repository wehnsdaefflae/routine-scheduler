"""The AUTONOMOUS apply (D191 step 3): a `config_patch` the routine's own `config_confirm`
dial permits lands with no click.

The operator, 2026-10-19: *"build the server surface + D191's apply route: a decision's
config_patch may name {"server": {...}} with its own declared field list, applied autonomously
only for fields on that list, everything else still a click"*. Step 1 built the dial, step 2
the surfaces; this is the half that makes a permitted patch apply.

Two halves, and the seam between them is the point:

- the ENGINE never writes config. A permitted patch is QUEUED (`rsched/autoapply.py`) and the
  run is told on that turn, so it does not wait for an answer it will not get.
- the WEB layer applies, through the one validated writer (`api_routine_patch.apply_updates`),
  re-judging the dial at the boundary. R2702: a check living only in the caller's restraint is
  not a boundary.

The states these tests exist to forbid: a config change that neither applied NOR asked, and a
patch that applied although the dial did not permit it.
"""

from __future__ import annotations

import yaml

from conftest import finish
from helpers import server_for, transcript_events
from rsched import autoapply
from rsched.engine.runtime import run_routine
from rsched.web import autoapply_drain

TS = "20261021-090000"
OWN = {"config_confirm": "own_fields"}


def _ask(patch, **extra):
    return {"say": "tune myself", "kind": "ask_user", "mode": "deferred",
            "question": "Raise my turn budget?", "config_patch": patch, **extra}


def _queued(home, slug):
    return [autoapply.read_request(p) for p in autoapply.pending(home, slug)]


def _with_caps(d, caps):
    """Set the routine's capabilities mapping on disk — `make_routine` writes none, and the
    dial is read from the routine's own config (`configdial.level_of`)."""
    path = d / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["capabilities"] = {"actions": [], "utils": [], **caps}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return d


def _run_with(make_routine, scripted, caps, actions, slug="tuner"):
    d = _with_caps(make_routine(slug=slug), caps)
    scripted([*actions, finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    return d, status, run_dir


# ---- the engine half: queued, and the run is told -----------------------------------------

def test_a_permitted_patch_is_queued_and_files_no_card(make_routine, scripted):
    d, status, run_dir = _run_with(make_routine, scripted, OWN,
                                   [_ask({"budgets": {"max_turns": 600}})])
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json")), "no card is owed"
    rows = _queued(d.parent, "tuner")
    assert len(rows) == 1
    assert rows[0]["patch"] == {"budgets": {"max_turns": 600}}
    assert rows[0]["level"] == "own_fields"
    # the run learns on the turn it asked — the whole point of the dial
    obs = [e for e in transcript_events(run_dir) if e["type"] == "observation"]
    assert any(e["payload"].get("config_applied") == "queued" for e in obs), obs


def test_a_field_outside_the_allow_list_still_asks_and_says_why(make_routine, scripted):
    """`schedule` is deliberately NOT in `own_fields` (a routine must not quietly stop
    running), so the card stands and the run is told which level would be needed."""
    d, status, _ = _run_with(make_routine, scripted, OWN,
                             [_ask({"schedule": {"friendly": {"frequency": "daily"}}})])
    assert status == "ok"
    assert len(list((d / "questions" / "pending").glob("*.json"))) == 1
    assert not _queued(d.parent, "tuner")


def test_a_sealed_field_is_never_autonomous_even_at_never(make_routine, scripted):
    """THE seal: no dial value lets a run widen its own authority surface."""
    d, _status, _ = _run_with(make_routine, scripted, {"config_confirm": "never"},
                              [_ask({"permissions": ["shell"]})])
    assert len(list((d / "questions" / "pending").glob("*.json"))) == 1
    assert not _queued(d.parent, "tuner")


def test_the_default_dial_changes_nothing(make_routine, scripted):
    """Installing this surface must not move a single routine: `always` is every routine's
    default, and at `always` every patch is still a card."""
    d, _status, _ = _run_with(make_routine, scripted, {},
                              [_ask({"budgets": {"max_turns": 600}})])
    assert len(list((d / "questions" / "pending").glob("*.json"))) == 1
    assert not _queued(d.parent, "tuner")


def test_an_ask_that_also_needs_an_answer_keeps_its_card(make_routine, scripted):
    """A patch riding a question with OPTIONS is not the whole of what the run asked: applying
    it would settle a record whose other half nobody answered."""
    d, _status, _ = _run_with(make_routine, scripted, OWN,
                              [_ask({"budgets": {"max_turns": 600}},
                                    options=["raise it", "leave it"])])
    assert len(list((d / "questions" / "pending").glob("*.json"))) == 1
    assert not _queued(d.parent, "tuner")


def test_the_server_surface_is_not_a_routines_dial(make_routine, scripted):
    """`{"server": {...}}` is the INSTANCE's config. `config_confirm` is per-routine, so no
    value of it speaks for the instance — that patch keeps its click."""
    d, _status, _ = _run_with(make_routine, scripted, {"config_confirm": "never"},
                              [_ask({"server": {"max_concurrent_runs": 3}})])
    assert len(list((d / "questions" / "pending").glob("*.json"))) == 1
    assert not _queued(d.parent, "tuner")


# ---- the web half: the drain applies through the one writer -------------------------------

def _app_for(d):
    """A hermetic app over this routine's home — the drain's one dependency is app.state."""
    from fastapi.testclient import TestClient

    from rsched.web.app import create_app

    client = TestClient(create_app(server_for(d), with_scheduler=False))
    client.__enter__()
    return client, client.app


def test_the_drain_applies_the_queued_patch_and_clears_the_spool(make_routine, scripted):
    d, _status, _ = _run_with(make_routine, scripted, OWN,
                              [_ask({"budgets": {"max_turns": 600}})])
    client, app = _app_for(d)
    try:
        rows = autoapply_drain.drain_all(app)
    finally:
        client.__exit__(None, None, None)
    assert rows and rows[0]["ok"], rows
    assert rows[0]["updated"] == ["budgets"]
    saved = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert saved["budgets"]["max_turns"] == 600          # it really landed in the file
    assert not autoapply.pending(d.parent, "tuner")      # and the request is consumed


def test_a_dial_turned_down_after_the_ask_stops_the_apply_and_files_the_card(
        make_routine, scripted):
    """The boundary check is the APPLY's, not the asking run's: a request queued while the
    dial permitted it must not apply once the operator has turned the dial down."""
    d, _status, _ = _run_with(make_routine, scripted, OWN,
                              [_ask({"budgets": {"max_turns": 600}})])
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["capabilities"]["config_confirm"] = "always"
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    client, app = _app_for(d)
    try:
        rows = autoapply_drain.drain_all(app)
    finally:
        client.__exit__(None, None, None)
    assert rows and not rows[0]["ok"] and "always" in rows[0]["refused"], rows
    saved = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert (saved.get("budgets") or {}).get("max_turns") != 600
    # neither applied nor silently dropped: the operator gets the card after all
    cards = list((d / "questions" / "pending").glob("*.json"))
    assert len(cards) == 1
    assert "autonomous apply was refused" in cards[0].read_text(encoding="utf-8")
    assert not autoapply.pending(d.parent, "tuner")


def test_the_spool_has_a_cap_and_the_ask_falls_back_to_a_card(make_routine, scripted):
    """A run filing these in a loop is a defect in that run, not a reason to fill the disk."""
    d = _with_caps(make_routine(slug="tuner"), OWN)
    for _ in range(autoapply.MAX_PENDING):
        assert autoapply.queue(d.parent, "tuner", patch={"budgets": {"max_turns": 1}},
                               level="own_fields", qid="q-x", run_id="r", question="?")
    assert autoapply.queue(d.parent, "tuner", patch={"budgets": {"max_turns": 1}},
                           level="own_fields", qid="q-y", run_id="r", question="?") is None
