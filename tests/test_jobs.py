"""The job STORE and the due computation (rsched/jobs.py), the setting that switches the layer
on (grants), and the gate pieces that feed it: a check's `job:` tag, the hub-feedback label and
the kit's `admit_reason` (gatekit), and the job clock admitting a fire (daemon/gate_prepare).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rsched import gatekit, jobs
from rsched.gatekit import run as kit
from rsched.grants import effective_settings, normalize_capabilities

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
TODAY = NOW.date()


def _job(tid, **kw):
    return {"id": tid, "title": tid.upper(), "brief": "b", "state": "active", **kw}


def _doc(*ts):
    return {"version": 1, "jobs": list(ts), "deleted": [], "run": {}}


CHECKS = [{"id": "a-mail", "kind": "mail", "job": "a"},
          {"id": "b-feedback", "kind": "hub_feedback", "job": "b"},
          {"id": "routine-wide", "kind": "url_changed"}]


def _gate(*working):
    return {"decision": "run", "reason": "x",
            "checks": [{"id": c["id"], "kind": c["kind"], "work": c["id"] in working,
                        "reason": "found something" if c["id"] in working else "nothing"}
                       for c in CHECKS]}


def _due(doc, gate):
    due, resting = jobs.due_for_run(doc, gate=gate, checks=CHECKS, today=TODAY, now=NOW)
    return [d["id"] for d in due], [r["id"] for r in resting]


# ── due ───────────────────────────────────────────────────────────────────────────────────

def test_a_tagged_check_that_found_work_makes_only_its_job_due():
    due, resting = _due(_doc(_job("a"), _job("b")), _gate("a-mail"))
    assert (due, resting) == (["a"], ["b"])


def test_an_untagged_check_that_found_work_makes_every_job_due():
    due, _ = _due(_doc(_job("a"), _job("b")), _gate("routine-wide"))
    assert due == ["a", "b"]


@pytest.mark.parametrize("gate", [None, {"decision": "bypass", "reason": "scope"},
                                  {"decision": "run", "reason": "pending inbox"}])
def test_a_fire_whose_checks_were_not_asked_makes_every_job_due(gate):
    """Fail-open: nothing measured which jobs are quiet, so none may be skipped."""
    due, resting = _due(_doc(_job("a"), _job("b")), gate)
    assert (due, resting) == (["a", "b"], [])


def test_paused_and_done_jobs_are_never_due():
    due, resting = _due(_doc(_job("a", state="paused"), _job("b", state="done")), None)
    assert (due, resting) == ([], [])


def test_a_job_no_check_watches_is_due_unless_it_rests_until_a_wake_date():
    doc = _doc(_job("c"), _job("d", wake=(TODAY + timedelta(days=3)).isoformat()),
               _job("e", wake=TODAY.isoformat()))
    due, resting = _due(doc, _gate())
    assert due == ["c", "e"] and resting == ["d"]


def test_carry_and_the_quiet_limit_make_a_watched_job_due_by_its_clock():
    stale = (NOW - timedelta(days=8)).isoformat()
    fresh = (NOW - timedelta(days=2)).isoformat()
    doc = _doc(_job("a", carry="deferred by r1: waits"),
               _job("b", quiet_days=7, last={"at": stale}))
    due, _ = _due(doc, _gate())
    assert due == ["a", "b"]
    doc = _doc(_job("a"), _job("b", quiet_days=7, last={"at": fresh}))
    assert _due(doc, _gate()) == ([], ["a", "b"])


def test_clock_due_is_what_admits_a_fire(tmp_path):
    (tmp_path / "state").mkdir()
    jobs.save(tmp_path, _doc(_job("a", carry="left over"), _job("b")))
    assert jobs.clock_due(tmp_path, CHECKS, today=TODAY, now=NOW) == [
        ("a", "carried over: left over")]


# ── recording ─────────────────────────────────────────────────────────────────────────────

def test_a_deferred_checkpoint_carries_the_job_and_any_other_settles_it():
    job = _job("a", carry="old", wake=TODAY.isoformat())
    jobs.record_checkpoint(job, run_id="r:1", outcome="deferred", summary="later", today=TODAY)
    assert job["carry"].startswith("deferred by r:1") and job["last"]["outcome"] == "deferred"
    assert "wake" not in job          # a wake date that has come is spent by processing
    jobs.record_checkpoint(job, run_id="r:2", outcome="advanced", summary="done",
                            wake="2026-10-20", today=TODAY)
    assert "carry" not in job and job["wake"] == "2026-10-20"
    assert [h["run"] for h in job["history"]] == ["r:2", "r:1"]


def test_carry_unprocessed_marks_what_the_run_owed_and_never_checkpointed(tmp_path):
    (tmp_path / "state").mkdir()
    doc = _doc(_job("a"), _job("b"), _job("c", state="paused"))
    doc["run"] = {"run_id": "r:1", "due": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                  "done": {"a": "advanced"}, "open": "b"}
    jobs.save(tmp_path, doc)
    assert jobs.carry_unprocessed(tmp_path, "r:1", "ended (partial)") == ["b"]
    back = jobs.load(tmp_path)
    assert jobs.find(back, "b")["carry"] == "run r:1 ended (partial) before checkpointing it"
    assert back["run"]["open"] == "" and back["run"]["ended"]
    assert jobs.carry_unprocessed(tmp_path, "r:other", "x") == []   # not this run's ledger


def test_store_problems_name_a_broken_file(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state/jobs.json").write_text('{"jobs": [{"id": "A B"}, {"id": "x", '
                                              '"state": "gone"}]}')
    assert jobs.problems(tmp_path) == ["jobs.json: job #1 has no kebab-case id",
                                        "jobs.json: job 'x' has state 'gone'"]


@pytest.mark.parametrize(("rel", "ok"), [("jobs/x", True), ("work/x", True),
                                         ("state/x", False), ("../x", False), ("/abs", False),
                                         (".memory", False), (".doc_cache/x", False)])
def test_a_workspace_stays_inside_the_routine_and_off_its_own_places(rel, ok):
    assert (jobs.workspace_problem(rel) == "") is ok


# ── the setting ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "want"), [("on", "on"), ("off", "off"), (True, "on"),
                                           (False, "off")])
def test_the_jobs_setting_reads_on_or_off_whatever_yaml_made_of_it(raw, want):
    caps, problems = normalize_capabilities({"jobs": raw})
    assert problems == [] and effective_settings(caps)["jobs"] == want


def test_a_bad_jobs_value_is_reported_and_reads_off():
    caps, problems = normalize_capabilities({"jobs": "sometimes"})
    assert problems == ["capabilities.jobs must be off or on"]
    assert effective_settings(caps)["jobs"] == "off"


def test_a_permission_doc_cannot_require_the_job_layer():
    _caps, problems = normalize_capabilities({"jobs": "on"}, label="requires", requires=True)
    assert problems and "setting the user chooses" in problems[0]


# ── the gate kit ──────────────────────────────────────────────────────────────────────────

def test_a_check_may_name_the_job_it_watches_but_never_for_the_routines_own_backstop():
    ok = [{"id": "m", "kind": "weekdays", "days": [0], "job": "nanogeofeld"}]
    assert gatekit.validate(ok) == []
    assert gatekit.validate([{"kind": "weekdays", "days": [0], "job": "Not A Slug"}]) == [
        "run_gate.checks[0]: job is the kebab-case id of the job this check watches"]
    assert "give the job quiet_days" in gatekit.validate(
        [{"kind": "max_quiet", "days": 3, "job": "a"}])[0]


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

def test_a_job_due_by_its_clock_admits_a_fire_its_checks_would_skip(tmp_path):
    from rsched.config.routine import RoutineConfig
    from rsched.daemon import gate_prepare

    (tmp_path / "state").mkdir()
    cfg = RoutineConfig.model_validate({
        "name": "r", "slug": "r", "dir": str(tmp_path),
        "capabilities": {"jobs": "on"},
        "run_gate": {"enabled": True, "checks": [{"kind": "weekdays", "days": [0], "job": "a"}]},
    })
    assert gate_prepare.keeps_jobs(cfg)
    assert gate_prepare.jobs_clock_reason(cfg) == ""
    jobs.save(tmp_path, _doc(_job("a", carry="deferred by r:1: later")))
    assert gate_prepare.jobs_clock_reason(cfg) == (
        "job(s) due by their own clock: a (carried over: deferred by r:1: later)")
    off = cfg.model_copy(update={"capabilities": {}})
    assert not gate_prepare.keeps_jobs(off)


def test_parse_day_reads_a_date_or_nothing():
    assert jobs.parse_day("2026-10-20T08:00") == date(2026, 10, 20)
    assert jobs.parse_day("soon") is None
