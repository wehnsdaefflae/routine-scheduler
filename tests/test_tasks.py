"""The task STORE and the due computation (rsched/tasks.py), the setting that switches the layer
on (grants), and the gate pieces that feed it: a check's `task:` tag, the hub-feedback label and
the kit's `admit_reason` (gatekit), and the task clock admitting a fire (daemon/gate_prepare).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rsched import gatekit, tasks
from rsched.gatekit import run as kit
from rsched.grants import effective_settings, normalize_capabilities

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
TODAY = NOW.date()


def _task(tid, **kw):
    return {"id": tid, "title": tid.upper(), "brief": "b", "state": "active", **kw}


def _doc(*ts):
    return {"version": 1, "tasks": list(ts), "deleted": [], "run": {}}


CHECKS = [{"id": "a-mail", "kind": "mail", "task": "a"},
          {"id": "b-feedback", "kind": "hub_feedback", "task": "b"},
          {"id": "routine-wide", "kind": "url_changed"}]


def _gate(*working):
    return {"decision": "run", "reason": "x",
            "checks": [{"id": c["id"], "kind": c["kind"], "work": c["id"] in working,
                        "reason": "found something" if c["id"] in working else "nothing"}
                       for c in CHECKS]}


def _due(doc, gate):
    due, resting = tasks.due_for_run(doc, gate=gate, checks=CHECKS, today=TODAY, now=NOW)
    return [d["id"] for d in due], [r["id"] for r in resting]


# ── due ───────────────────────────────────────────────────────────────────────────────────

def test_a_tagged_check_that_found_work_makes_only_its_task_due():
    due, resting = _due(_doc(_task("a"), _task("b")), _gate("a-mail"))
    assert (due, resting) == (["a"], ["b"])


def test_an_untagged_check_that_found_work_makes_every_task_due():
    due, _ = _due(_doc(_task("a"), _task("b")), _gate("routine-wide"))
    assert due == ["a", "b"]


@pytest.mark.parametrize("gate", [None, {"decision": "bypass", "reason": "scope"},
                                  {"decision": "run", "reason": "pending inbox"}])
def test_a_fire_whose_checks_were_not_asked_makes_every_task_due(gate):
    """Fail-open: nothing measured which tasks are quiet, so none may be skipped."""
    due, resting = _due(_doc(_task("a"), _task("b")), gate)
    assert (due, resting) == (["a", "b"], [])


def test_paused_and_done_tasks_are_never_due():
    due, resting = _due(_doc(_task("a", state="paused"), _task("b", state="done")), None)
    assert (due, resting) == ([], [])


def test_a_task_no_check_watches_is_due_unless_it_rests_until_a_wake_date():
    doc = _doc(_task("c"), _task("d", wake=(TODAY + timedelta(days=3)).isoformat()),
               _task("e", wake=TODAY.isoformat()))
    due, resting = _due(doc, _gate())
    assert due == ["c", "e"] and resting == ["d"]


def test_carry_and_the_quiet_limit_make_a_watched_task_due_by_its_clock():
    stale = (NOW - timedelta(days=8)).isoformat()
    fresh = (NOW - timedelta(days=2)).isoformat()
    doc = _doc(_task("a", carry="deferred by r1: waits"),
               _task("b", quiet_days=7, last={"at": stale}))
    due, _ = _due(doc, _gate())
    assert due == ["a", "b"]
    doc = _doc(_task("a"), _task("b", quiet_days=7, last={"at": fresh}))
    assert _due(doc, _gate()) == ([], ["a", "b"])


def test_clock_due_is_what_admits_a_fire(tmp_path):
    (tmp_path / "state").mkdir()
    tasks.save(tmp_path, _doc(_task("a", carry="left over"), _task("b")))
    assert tasks.clock_due(tmp_path, CHECKS, today=TODAY, now=NOW) == [
        ("a", "carried over: left over")]


# ── recording ─────────────────────────────────────────────────────────────────────────────

def test_a_deferred_checkpoint_carries_the_task_and_any_other_settles_it():
    task = _task("a", carry="old", wake=TODAY.isoformat())
    tasks.record_checkpoint(task, run_id="r:1", outcome="deferred", summary="later", today=TODAY)
    assert task["carry"].startswith("deferred by r:1") and task["last"]["outcome"] == "deferred"
    assert "wake" not in task          # a wake date that has come is spent by processing
    tasks.record_checkpoint(task, run_id="r:2", outcome="advanced", summary="done",
                            wake="2026-10-20", today=TODAY)
    assert "carry" not in task and task["wake"] == "2026-10-20"
    assert [h["run"] for h in task["history"]] == ["r:2", "r:1"]


def test_carry_unprocessed_marks_what_the_run_owed_and_never_checkpointed(tmp_path):
    (tmp_path / "state").mkdir()
    doc = _doc(_task("a"), _task("b"), _task("c", state="paused"))
    doc["run"] = {"run_id": "r:1", "due": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                  "done": {"a": "advanced"}, "open": "b"}
    tasks.save(tmp_path, doc)
    assert tasks.carry_unprocessed(tmp_path, "r:1", "ended (partial)") == ["b"]
    back = tasks.load(tmp_path)
    assert tasks.find(back, "b")["carry"] == "run r:1 ended (partial) before checkpointing it"
    assert back["run"]["open"] == "" and back["run"]["ended"]
    assert tasks.carry_unprocessed(tmp_path, "r:other", "x") == []   # not this run's ledger


def test_store_problems_name_a_broken_file(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state/tasks.json").write_text('{"tasks": [{"id": "A B"}, {"id": "x", '
                                              '"state": "gone"}]}')
    assert tasks.problems(tmp_path) == ["state/tasks.json: task #1 has no kebab-case id",
                                        "state/tasks.json: task 'x' has state 'gone'"]


@pytest.mark.parametrize(("rel", "ok"), [("tasks/x", True), ("work/x", True),
                                         ("state/x", False), ("../x", False), ("/abs", False),
                                         (".memory", False)])
def test_a_workspace_stays_inside_the_routine_and_off_its_own_places(rel, ok):
    assert (tasks.workspace_problem(rel) == "") is ok


# ── the setting ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "want"), [("on", "on"), ("off", "off"), (True, "on"),
                                           (False, "off")])
def test_the_tasks_setting_reads_on_or_off_whatever_yaml_made_of_it(raw, want):
    caps, problems = normalize_capabilities({"tasks": raw})
    assert problems == [] and effective_settings(caps)["tasks"] == want


def test_a_bad_tasks_value_is_reported_and_reads_off():
    caps, problems = normalize_capabilities({"tasks": "sometimes"})
    assert problems == ["capabilities.tasks must be off or on"]
    assert effective_settings(caps)["tasks"] == "off"


def test_a_permission_doc_cannot_require_the_task_layer():
    _caps, problems = normalize_capabilities({"tasks": "on"}, label="requires", requires=True)
    assert problems and "setting the user chooses" in problems[0]


# ── the gate kit ──────────────────────────────────────────────────────────────────────────

def test_a_check_may_name_the_task_it_watches_but_never_for_the_routines_own_backstop():
    ok = [{"id": "m", "kind": "weekdays", "days": [0], "task": "nanogeofeld"}]
    assert gatekit.validate(ok) == []
    assert gatekit.validate([{"kind": "weekdays", "days": [0], "task": "Not A Slug"}]) == [
        "run_gate.checks[0]: task is the kebab-case id of the task this check watches"]
    assert "give the task quiet_days" in gatekit.validate(
        [{"kind": "max_quiet", "days": 3, "task": "a"}])[0]


def test_an_admit_reason_runs_whatever_the_checks_say_and_keeps_their_answers(tmp_path):
    (tmp_path / "routine").mkdir()
    ctx = {"version": 2, "routine": "me", "routine_dir": str(tmp_path / "routine"),
           "routines_home": str(tmp_path), "now": NOW.isoformat(), "last_ok": None,
           "checks": [{"id": "w", "kind": "weekdays", "days": [(TODAY.weekday() + 1) % 7]}],
           "admit_reason": "pending inbox"}
    out = kit.evaluate(ctx)
    assert out["decision"] == "run" and out["reason"].startswith("pending inbox")
    assert out["checks"][0]["id"] == "w" and out["checks"][0]["work"] is False


def test_hub_feedback_with_a_label_counts_only_that_sections_rows(monkeypatch):
    from rsched.gatekit import kit_net  # after `run`, which puts the kit's siblings on the path

    answer = {"ok": True, "count": 4, "pending": [{"id": "fau · ards · direction"},
                                                  {"id": "fau · nanogeofeld/question/x"},
                                                  {"id": "fau · ards/doc/report"},
                                                  {"id": "fau · nanogeofeld/doc/standards"}]}
    monkeypatch.setattr(kit_net, "web_login", lambda *_a: {"host": "h", "user": "u"})
    monkeypatch.setattr(kit_net, "_hub_token", lambda _ctx: "t")
    monkeypatch.setattr(kit_net, "_get", lambda *_a: __import__("json").dumps(answer).encode())
    work, reason, _ = kit_net.hub_feedback({"project": "fau", "label": "ards"}, {})
    assert work and reason == "2 feedback entries in the 'ards' section of the hub page"
    work, reason, _ = kit_net.hub_feedback({"project": "fau", "label": "suedlink-wlf"}, {})
    assert not work and "section" in reason
    work, reason, _ = kit_net.hub_feedback({"project": "fau"}, {})
    assert work and reason == "4 feedback entries on the hub page"


# ── admission ─────────────────────────────────────────────────────────────────────────────

def test_a_task_due_by_its_clock_admits_a_fire_its_checks_would_skip(tmp_path):
    from rsched.config.routine import RoutineConfig
    from rsched.daemon import gate_prepare

    (tmp_path / "state").mkdir()
    cfg = RoutineConfig.model_validate({
        "name": "r", "slug": "r", "dir": str(tmp_path),
        "capabilities": {"tasks": "on"},
        "run_gate": {"enabled": True, "checks": [{"kind": "weekdays", "days": [0], "task": "a"}]},
    })
    assert gate_prepare.keeps_tasks(cfg)
    assert gate_prepare.tasks_clock_reason(cfg) == ""
    tasks.save(tmp_path, _doc(_task("a", carry="deferred by r:1: later")))
    assert gate_prepare.tasks_clock_reason(cfg) == (
        "task(s) due by their own clock: a (carried over: deferred by r:1: later)")
    off = cfg.model_copy(update={"capabilities": {}})
    assert not gate_prepare.keeps_tasks(off)


def test_parse_day_reads_a_date_or_nothing():
    assert tasks.parse_day("2026-10-20T08:00") == date(2026, 10, 20)
    assert tasks.parse_day("soon") is None
