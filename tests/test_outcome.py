"""The OUTCOME contract — what a routine exists to produce, declared, measured every run, judged.

freelance-radar met every Done-when line it had for four months while 89 applications won zero
interviews: every judge read the routine's own account. These tests pin the three halves that
replace that account with a number — the declaration (config/outcome.py), the measurement at
every run's end (engine/outcome.py) and the series it builds against cost
(readmodels/outcome_series.py) — and the setup-surface row that fails `rsched validate` for a
routine missing any of them (readmodels/surface_outcome.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from conftest import authed_client, finish, make_test_server
from helpers import declare_outcome, server_for, write_usage_stream
from rsched.config import load_routine
from rsched.config.outcome import OutcomeConfig
from rsched.engine import outcome as outcome_mod
from rsched.engine.runtime import run_routine
from rsched.readmodels.outcome_series import review
from rsched.readmodels.surface_outcome import _outcome_nodes, names_a_measure

TS = "20261017-070000"
LOOK = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}
#: a run of a declared routine answers for its Done-when line naming the measure — `unmet`, so
#: the claim verifier (a model call) is not consulted
DONE = {**finish(), "accounting": ["d1 unmet: nothing to show yet"]}
CONTRACT = {"principal": "Mark", "measures": [
    {"id": "interviews", "label": "interviews or calls booked with a client"},
    {"id": "contracts", "label": "signed contracts"}]}


def _server(tmp_path):
    return SimpleNamespace(routines_home=tmp_path / "routines",
                           conversations_home=tmp_path / "conversations")


# ---- the declaration -----------------------------------------------------------------------

def test_a_contract_names_its_principal_and_at_least_one_measure():
    assert OutcomeConfig.model_validate(CONTRACT).measures[1].kind == "count"
    for bad in ({"principal": "Mark", "measures": []},
                {"measures": CONTRACT["measures"]},
                {**CONTRACT, "measures": [*CONTRACT["measures"], CONTRACT["measures"][0]]},
                {**CONTRACT, "measures": [{"id": "Weight KG", "label": "kg"}]},
                {**CONTRACT, "extra": 1}):
        with pytest.raises(ValidationError):
            OutcomeConfig.model_validate(bad)


def test_a_bad_contract_is_a_load_problem_and_reads_as_undeclared(make_routine):
    d = make_routine(slug="badout")
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    raw["outcome"] = {"principal": "Mark", "measures": []}
    (d / "routine.yaml").write_text(yaml.safe_dump(raw))
    cfg, problems = load_routine(d)
    assert cfg is not None and cfg.outcome is None
    assert any("outcome" in p for p in problems)


# ---- the setup surface ---------------------------------------------------------------------

def test_each_missing_half_is_a_blocking_row_naming_what_is_missing(tmp_path, make_routine):
    d = make_routine(slug="hunter")
    server = _server(tmp_path)

    def row():
        cfg, _ = load_routine(d)
        return _outcome_nodes(server, cfg)

    first = row()
    assert [(n["id"], n["state"], n["severity"]) for n in first] == \
        [("recipe:outcome", "undeclared", "blocks")]
    assert first[0]["fix"] == {"kind": "declare_outcome",
                               "missing": "an outcome contract in routine.yaml"}
    assert first[0]["source"] == {"recipe": "outcome"}     # never library drift
    declare_outcome(d)
    assert row() == []
    (d / "scripts" / "outcome.py").unlink()
    assert row()[0]["state"] == "unmeasured"
    declare_outcome(d)
    text = (d / "main.md").read_text().replace("this month's replies", "this run's state")
    (d / "main.md").write_text(text)
    assert row()[0]["state"] == "process-only"


def test_a_routine_that_starts_no_run_or_is_a_conversation_owes_no_outcome(tmp_path,
                                                                         make_routine):
    d = make_routine(slug="off")
    cfg, _ = load_routine(d)
    cfg.enabled = False
    assert _outcome_nodes(_server(tmp_path), cfg) == []
    conv = SimpleNamespace(slug="c-1", dir=tmp_path / "conversations" / "c-1", enabled=True)
    (conv.dir).mkdir(parents=True)
    (conv.dir / "main.md").write_text("x")
    assert _outcome_nodes(_server(tmp_path), conv) == []


def test_a_done_when_line_names_a_measure_only_as_a_word():
    assert names_a_measure("the page leads with this month's interviews", ["interviews"])
    assert names_a_measure("money_won rose", ["money_won"])
    assert not names_a_measure("every interviewsheet was filed", ["interviews"])


def test_the_row_never_reaches_a_runs_boot_note(make_routine, scripted):
    """No run can declare its own success, and the gap fails no call a run makes — the row is
    the operator's and the improver's (rsched validate, the routine page), never prompt noise."""
    d = make_routine(slug="nonote")
    scripted([LOOK, finish()])
    run_routine(d, server_for(d), run_ts=TS)
    text = (d / "runs" / TS / "transcript.jsonl").read_text()
    assert "recipe:outcome" not in text


# ---- the measurement -----------------------------------------------------------------------

def test_the_script_is_held_to_shape_only():
    measures = [{"id": "interviews"}, {"id": "contracts"}]
    assert outcome_mod.parse('noise\n{"interviews": 2, "contracts": 0}\n', measures) == \
        {"values": {"interviews": 2, "contracts": 0}}
    assert outcome_mod.parse('{"interviews": 2}', measures)["error"] == "no number for contracts"
    assert "no JSON object" in outcome_mod.parse("done", measures)["error"]
    assert "contracts" in outcome_mod.parse('{"interviews": 1, "contracts": true}',
                                            measures)["error"]


def test_every_run_records_its_outcome_beside_its_cost(make_routine, scripted):
    d = make_routine(slug="measured")
    declare_outcome(d)
    scripted([LOOK, DONE])
    run_routine(d, server_for(d), run_ts=TS)
    status = json.loads((d / "runs" / TS / "status.json").read_text())
    assert status["outcome_measures"] == {"values": {"replies": 3}}
    rows = [json.loads(x) for x in
            (d.parent / ".control" / "workflow-usage.jsonl").read_text().splitlines()]
    top = next(r for r in rows if not r["depth"])
    assert top["outcome_measures"] == {"values": {"replies": 3}}
    assert "volume" in top and "notional_cost" in top


def test_a_failing_script_records_an_error_never_a_zero(make_routine, scripted):
    d = make_routine(slug="broken")
    declare_outcome(d, script="import sys\nsys.exit(3)\n")
    scripted([LOOK, DONE])
    run_routine(d, server_for(d), run_ts=TS)
    measured = json.loads((d / "runs" / TS / "status.json").read_text())["outcome_measures"]
    assert "exited 3" in measured["error"] and "values" not in measured


# ---- the series ----------------------------------------------------------------------------

def test_the_review_reads_the_series_against_cost(tmp_path):
    home = tmp_path / "routines"
    (home / "hunter").mkdir(parents=True)

    def run(day, interviews, volume, notional):
        return {"ts": f"2026-10-{day:02d}T07:00:00+02:00", "routine": "hunter",
                "run_id": f"hunter:202610{day:02d}-050000", "depth": 0, "status": "ok",
                "turns": 50, "volume": volume, "notional_cost": notional,
                "outcome_measures": {"values": {"interviews": interviews, "contracts": 0}}}

    write_usage_stream(home, [run(1, 0, 30_000_000, None), run(8, 0, 40_000_000, 20.0),
                              run(15, 1, 30_000_000, 15.0),
                              {**run(16, 0, None, None), "outcome_measures": {"error": "x"}}])
    cfg = SimpleNamespace(slug="hunter", outcome=OutcomeConfig.model_validate(CONTRACT))
    out = review(home, cfg, weeks=4, now=datetime(2026, 10, 17, tzinfo=UTC))
    interviews, contracts = out["measures"]
    assert (interviews["first"], interviews["last"], interviews["change"]) == (0, 1, 1)
    assert interviews["per_unit"] == 100_000_000           # the window's volume per interview
    assert contracts["change"] == 0 and contracts["per_unit"] is None
    assert out["cost"] == {"runs": 4, "volume": 100_000_000, "unmeasured_runs": 1,
                           "notional": 35.0, "notional_runs": 2}
    assert out["unreadable_runs"] == 1


# ---- the config surface --------------------------------------------------------------------

def test_the_patch_sets_and_clears_a_contract(tmp_path, make_routine):
    d = make_routine(slug="webo")
    with authed_client(make_test_server(tmp_path)) as c:
        r = c.patch("/api/routines/webo", json={"outcome": CONTRACT})
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == ["outcome"]
        saved = yaml.safe_load((d / "routine.yaml").read_text())["outcome"]
        assert saved["measures"][0]["id"] == "interviews"
        assert c.patch("/api/routines/webo",
                       json={"outcome": {"principal": "Mark"}}).status_code == 422
        r = c.patch("/api/routines/webo", json={"outcome": None})
        assert r.status_code == 200, r.text
        assert load_routine(d)[0].outcome is None
