"""The NARROW revise leg (engine/reviseleg.py): an approved recipe-revision card buys an EDIT.

Each test pins one half of what the leg is NOT any more — a whole run that re-orients, owes the
recipe's accounting again, carries the routine's full toolset and budget, and closes out as the
run's new verdict over the summary of the work it was resumed after. And one control: the run
view's checkbox (a FULL revise leg) and an ordinary resume keep everything they had.
"""

from __future__ import annotations

from types import SimpleNamespace

from conftest import finish
from helpers import health_events, server_for
from rsched.engine import finish_rungs, reviseleg
from rsched.engine.inbox import file_message
from rsched.engine.revise import NARROW, write_revise_marker
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import read_json
from rsched.readmodels.usage_stream import fold_legs, usage_records
from rsched.usage_log import REVISE_LEG

TS = "20261106-080000"
DONE_WHEN = "\n## Done when\n- d1 — the weekly report is written to state/report.md\n"


def _read(path: str) -> dict:
    return {"say": "Reading it back.", "kind": "read_file", "path": path}


def _edit(path: str, anchor: str, replacement: str) -> dict:
    return {"say": "Making the approved edit.", "kind": "edit_file", "path": path,
            "anchor": anchor, "replacement": replacement}


def _probe() -> dict:
    return {"say": "Doing the task.", "kind": "write_file", "path": "state/probe.txt",
            "content": "probe"}


def _arm(routine_dir, run_dir, *, narrow: bool = True) -> None:
    """What the card's approval files (web/api_recipe_revision.py): marker + live message."""
    text = "The operator approved: trim the run flow's step 2."
    write_revise_marker(run_dir, text, leg=NARROW if narrow else "")
    file_message(routine_dir, text, via="web-converse")


def _leg_events(run_dir) -> list[dict]:
    """The events of the LAST leg: everything after the last-but-one finish."""
    events, _ = read_events(run_dir / "transcript.jsonl")
    finishes = [i for i, e in enumerate(events) if e["type"] == "finish"]
    return events[finishes[-2] + 1:] if len(finishes) > 1 else events


def _schema_kinds(call: dict) -> set[str]:
    return set(call["schema"]["properties"]["kind"]["enum"])


def test_the_finish_owes_no_accounting_where_a_full_leg_would(make_routine, scripted):
    """A proposing leg the engine ended (no authored finish) leaves a resume that owes the
    recipe's Done-when again — the FULL leg's finish is deferred for it. The narrow leg's finish
    stands without one: the run's work was answered for, or not, by the leg that ran it."""
    from rsched.endpoints.base import EndpointError

    for narrow in (True, False):
        d = make_routine(slug=f"acct{int(narrow)}")
        server = server_for(d)
        scripted([_probe(), EndpointError("boom 401", auth=True)])
        status1, run_dir = run_routine(d, server, run_ts=TS)
        assert status1 == "failed"
        (d / "main.md").write_text((d / "main.md").read_text() + DONE_WHEN, encoding="utf-8")
        _arm(d, run_dir, narrow=narrow)
        scripted([_edit("main.md", "as few actions as possible", "as few actions as needed"),
                  finish(summary="main.md step 1 reworded"),
                  {**finish(summary="main.md step 1 reworded"),
                   "accounting": ["d1 unmet: this leg only reworded the recipe"]}])
        run_routine(d, server, run_ts=TS, resume_from=TS)
        deferred = [e for e in _leg_events(run_dir) if e["type"] == "observation"
                    and e["payload"].get("kind") == "finish" and e["payload"].get("rejected")]
        if narrow:
            assert deferred == []
        else:
            assert deferred and "accounting" in deferred[0]["payload"]


def test_the_leg_keeps_the_run_verdict_and_appends_to_its_result(make_routine, scripted):
    """The leg closes out the LEG: the run's verdict stays the proposing leg's on status.json,
    the usage fold and the health stream (no correction claiming the task was redone), and
    `result.md` gains the leg's line instead of losing the run's own summary to it."""
    d = make_routine(slug="keep")
    server = server_for(d)
    scripted([_probe(), finish(status="partial", summary="TASK SUMMARY: 3 of 5 sources read")])
    run_routine(d, server, run_ts=TS)
    run_dir = d / "runs" / TS
    _arm(d, run_dir)
    scripted([_edit("main.md", "as few actions as possible", "as few actions as needed"),
              _read("main.md"), finish(summary="main.md: step 1 reworded")])
    status, _ = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "partial"                                  # the run's verdict, not the leg's
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert result.startswith("TASK SUMMARY: 3 of 5 sources read")
    assert "**Recipe revision leg** (ok): main.md: step 1 reworded" in result
    fin = [e for e in _leg_events(run_dir) if e["type"] == "finish"][-1]["payload"]
    assert fin["status"] == "partial" and fin["revise_leg"] == {"status": "ok"}
    assert read_json(run_dir / "status.json")["state"] == "finished"
    rows = [r for r in usage_records(server.routines_home) if r["run_id"] == f"keep:{TS}"]
    assert [r.get("leg") for r in rows] == [None, REVISE_LEG]
    (run,) = fold_legs(rows)
    assert run["status"] == "partial" and run["volume"] == rows[0]["volume"] + rows[1]["volume"]
    kinds = [e["event"] for e in health_events(server.routines_home, routine="keep")]
    assert kinds == ["run_partial"]                             # no run_corrected row


def test_the_leg_holds_only_the_recipe_edit_kinds_and_no_rules(make_routine, scripted):
    d = make_routine(slug="tools")
    server = server_for(d)
    scripted([_probe(), finish(summary="task done")])
    run_routine(d, server, run_ts=TS)
    _arm(d, d / "runs" / TS)
    ep = scripted([finish(summary="nothing to change after all")])
    run_routine(d, server, run_ts=TS, resume_from=TS)
    assert _schema_kinds(ep.calls[0]) == {*reviseleg.LEG_KINDS, "list_models"}
    prompt = ep.calls[0]["messages"]
    assert "resumed for a RECIPE REVISION only" in prompt[-2]["content"]   # its own note…
    assert "the previous result plus" not in prompt[-2]["content"]       # …not the follow-up's


def test_the_turn_budget_is_a_small_backstop_with_the_reserved_finish(make_routine, scripted):
    """The routine's own 40 turns become LEG_TURNS; the reserved finish turn is unchanged, so
    a leg that runs past it is ended by the engine one turn later, and the run's verdict holds."""
    d = make_routine(slug="cap", budgets={"max_turns": 40})
    server = server_for(d)
    scripted([_probe(), finish(summary="task done")])
    run_routine(d, server, run_ts=TS)
    _arm(d, d / "runs" / TS)
    # distinct reads — five identical actions in a row end a run on their own (REPEAT_FAIL)
    ep = scripted([{**_read("main.md"), "start_line": i + 1}
                   for i in range(reviseleg.LEG_TURNS + 4)])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert len(ep.calls) == reviseleg.LEG_TURNS + 1             # the window, then the reserve
    fin = [e for e in _leg_events(run_dir) if e["type"] == "finish"][-1]["payload"]
    assert fin["revise_leg"] == {"status": "partial"} and status == "ok"
    kinds = [e["event"] for e in health_events(server.routines_home, routine="cap")]
    assert "budget_exhausted" not in kinds


def test_a_full_revise_leg_and_a_plain_resume_are_unchanged(make_routine, scripted):
    """The run view's checkbox writes no `leg`: its leg keeps the routine's toolset, and its
    ending is the run's verdict and its summary the run's result, exactly as before."""
    d = make_routine(slug="full")
    server = server_for(d)
    scripted([_probe(), finish(status="partial", summary="TASK SUMMARY")])
    run_routine(d, server, run_ts=TS)
    run_dir = d / "runs" / TS
    _arm(d, run_dir, narrow=False)
    ep = scripted([finish(summary="REPLACED")])
    status, _ = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert (run_dir / "result.md").read_text(encoding="utf-8") == "REPLACED\n"
    assert _schema_kinds(ep.calls[0]) > {*reviseleg.LEG_KINDS, "util", "memory_write"}
    assert "revise_leg" not in [e for e in _leg_events(run_dir)
                                if e["type"] == "finish"][-1]["payload"]


def test_the_task_rungs_are_declared_off_on_a_revise_leg():
    """The finish ladder's own table says which rungs a revise leg skips, by the rung's
    question: everything about the run's TASK. The message-delivery rungs and the fabrication
    and unbacked-claim guards still stand."""
    ctx = SimpleNamespace(depth=0)
    skipped = {"jobs_owed", "accounting", "assist", "claims_unsupported"}
    for key in finish_rungs.RUNG_KEYS:
        normal = finish_rungs.eligible(SimpleNamespace(_finish_reserved=False), ctx, key)
        leg = finish_rungs.eligible(
            SimpleNamespace(_finish_reserved=False, revise_leg=True), ctx, key)
        assert normal is True
        assert leg is (key not in skipped), key


def test_result_text_appends_and_survives_a_missing_result(tmp_path):
    assert reviseleg.result_text(tmp_path, " edited ", "ok") == \
        "**Recipe revision leg** (ok): edited\n"
    (tmp_path / "result.md").write_text("THE RUN\n", encoding="utf-8")
    once = reviseleg.result_text(tmp_path, "edit one", "ok")
    assert once == "THE RUN\n\n**Recipe revision leg** (ok): edit one\n"


def test_the_fold_reads_a_revise_row_as_spend_only():
    task = {"run_id": "r:1", "status": "partial", "turns": 9, "volume": 100, "tokens": 10,
            "quality": {"q": 1}, "outcome_measures": {"values": {"m": 3}}}
    edit = {"run_id": "r:1", "status": "ok", "turns": 12, "volume": 5, "tokens": 1,
            "quality": {"q": 2}, "leg": REVISE_LEG}
    (run,) = fold_legs([task, edit])
    assert run["status"] == "partial" and run["quality"] == {"q": 1}
    assert run["outcome_measures"] == {"values": {"m": 3}}
    assert run["turns"] == 12 and run["volume"] == 105 and run["tokens"] == 11


def test_the_leg_keeps_the_task_legs_turn_window_in_status_json(make_routine, scripted):
    """F895 (`config-optimizer`'s R3393, folded in by `scheduler-builder`'s R3404): the leg's
    own `write_status` REPLACES the budget block, so the task leg's ceiling and remainder were
    gone from the only file that held them and `base_turn` — its final cumulative turn — was the
    sole trace. Measured 2026-11-13: 20 leg-overwritten run dirs across 8 routines,
    `drawerrr-irreversible-actions` 6 of 6, and a 12-turn leg once read as a 154/150 ceiling hit
    that never happened. The window is now CARRIED, not recomputed.
    """
    d = make_routine(slug="legwin", budgets={"max_turns": 150})
    server = server_for(d)
    scripted([_probe(), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    task = read_json(run_dir / "status.json")["budgets"]
    assert (task["max_turns"], task["base_turn"]) == (150, 0)
    assert "legs" not in task                       # a run that never resumed carries none

    _arm(d, run_dir)
    scripted([_read("main.md"), finish()])
    assert run_routine(d, server, run_ts=TS, resume_from=TS)[0] == "ok"

    leg = read_json(run_dir / "status.json")["budgets"]
    assert leg["max_turns"] == reviseleg.LEG_TURNS          # the leg's own window, as before
    assert [(x["max_turns"], x["window_turns"], x["turns_left"]) for x in leg["legs"]] == [
        (150, task["window_turns"], task["turns_left"])]    # the task leg's, not recomputed
