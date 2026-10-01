"""A routine that reaches its FINISH LINE retires itself — without anything writing config.

The operator asked for routines that "disable themselves once they think they reached it". The
invariant it runs into is absolute: a run never writes `routine.yaml`, and the engine never writes
config. Retirement satisfies both because it is DERIVED — the scheduler simply builds no fire
table entry for a routine whose finish line is reached (`engine/finishline.py`); reopening it
puts the routine straight back. The `enabled: false` half is a click on the Decisions page,
through the ordinary config writer.

Three parties complete a finish line, each covered here: a RUN whose accounting proves an
outcome it judges, the CALENDAR (a date outcome or the `until` date — no run involved), and the
OPERATOR ticking an outcome only they judge.
"""

from __future__ import annotations

import yaml

from conftest import FakeRunner, finish
from helpers import tmp_server
from rsched import pending, registry
from rsched.config import ServerConfig
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler
from rsched.engine import finishline

NOW = "2026-09-05T09:00:00+02:00"


def _goal_met(routine_dir, text="the application is submitted"):
    """The operator's own tick on the one outcome only they judge."""
    finishline.save(routine_dir, {"outcomes": [{"text": text, "judge": "you",
                                                "status": "met"}]}, now=NOW)


# ---- the derived half: nothing is written, and the routine stops firing -------------------------

def test_a_routine_whose_finish_line_is_reached_gets_no_fire_table_entry(make_routine, tmp_path):
    d = make_routine(slug="finisher")
    sched = Scheduler(tmp_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "finisher" in sched.next_fires            # ordinary routine, ordinary cron

    _goal_met(d)
    sched.rescan()
    assert "finisher" not in sched.next_fires
    # and `enabled` was NOT touched — retirement is derived, not a config write
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8")).get(
        "enabled", True) is not False


def test_reopening_the_finish_line_puts_the_routine_straight_back(make_routine, tmp_path):
    d = make_routine(slug="reopened")
    _goal_met(d)
    sched = Scheduler(tmp_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "reopened" not in sched.next_fires

    finishline.reopen(d)
    sched.rescan()
    assert "reopened" in sched.next_fires


def test_a_done_when_line_never_retires_anything(make_routine, tmp_path):
    """What one run delivers is re-asked every run; only the finish line ends a routine."""
    d = make_routine(slug="perrun")
    (d / "main.md").write_text("# R\n\n## Done when\n\n- d1 — one increment landed\n",
                               encoding="utf-8")
    run = d / "runs" / "2026-09-04T09-00-00"
    run.mkdir(parents=True)
    (run / "status.json").write_text('{"accounting": ["d1 met: landed"]}', encoding="utf-8")
    sched = Scheduler(tmp_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "perrun" in sched.next_fires


async def test_boot_catchup_does_not_make_up_runs_for_a_finished_routine(make_routine, tmp_path):
    d = make_routine(slug="catchgoal")
    text = (d / "routine.yaml").read_text().replace("catchup: skip", "catchup: run_once")
    (d / "routine.yaml").write_text(text)
    _goal_met(d)
    fr = FakeRunner()
    sched = Scheduler(tmp_server(tmp_path), fr, EventBus())
    sched.rescan()
    await sched.boot_catchup()
    assert fr.fired == []


def test_the_registry_reports_retired_separately_from_disabled(make_routine, tmp_path):
    done = make_routine(slug="done")
    off = make_routine(slug="off")
    (off / "routine.yaml").write_text(
        (off / "routine.yaml").read_text().replace("enabled: true", "enabled: false"))
    _goal_met(done)
    catalog = registry.scan(tmp_server(tmp_path))
    assert catalog["done"].retired is True and catalog["done"].cfg.enabled is True
    assert catalog["off"].retired is False and catalog["off"].cfg.enabled is False


# ---- the calendar: no run is involved ------------------------------------------------------------

def test_a_passed_until_date_retires_the_routine_and_the_tick_queues_the_card(make_routine,
                                                                             tmp_path):
    d = make_routine(slug="deadline")
    finishline.save(d, {"outcomes": [{"text": "submitted", "judge": "run"}],
                        "until": "2026-01-31"}, now=NOW)
    server = tmp_server(tmp_path)
    sched = Scheduler(server, FakeRunner(), EventBus())
    sched.rescan()
    assert "deadline" not in sched.next_fires
    queued = pending.load_all(server.routines_home)
    assert [(q["kind"], q["routine"]) for q in queued] == [("goal-reached", "deadline")]
    assert queued[0]["fields"]["why"] == "its end date 2026-01-31 has passed"
    sched.rescan()                                   # queue-once: the next tick files nothing
    assert len(pending.load_all(server.routines_home)) == 1


def test_a_date_outcome_whose_day_has_come_is_reached(make_routine, tmp_path):
    d = make_routine(slug="closes")
    finishline.save(d, {"outcomes": [{"text": "the call closes", "judge": "date",
                                      "date": "2026-02-01"}]}, now=NOW)
    assert registry.scan(tmp_server(tmp_path))["closes"].retired is True


def test_a_date_that_names_no_day_cannot_take_the_catalog_down(make_routine, tmp_path):
    """`2026-02-30` has the YYYY-MM-DD shape. Kept, `reached` raised on it inside
    `registry.scan` — the listing every page and the scheduler's tick read — so one routine's
    hand-edited finish line stopped them all. It now reads as no date at all."""
    make_routine(slug="healthy")
    d = make_routine(slug="baddate")
    finishline.path(d).parent.mkdir(parents=True, exist_ok=True)
    finishline.path(d).write_text(
        '{"outcomes": [{"id": "g1", "text": "closes", "judge": "date", "date": "2026-02-30"}],'
        ' "until": "2026-02-31"}', encoding="utf-8")
    catalog = registry.scan(tmp_server(tmp_path))
    assert catalog["baddate"].retired is False and catalog["healthy"].retired is False


def test_a_future_until_leaves_the_routine_running(make_routine, tmp_path):
    d = make_routine(slug="later")
    finishline.save(d, {"outcomes": [], "until": "2999-01-01"}, now=NOW)
    sched = Scheduler(tmp_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "later" in sched.next_fires


# ---- lane chains: deliberately off is not a broken chain ----------------------------------------

async def test_a_retired_lane_member_is_skipped_without_counting_as_a_failure(tmp_path):
    """These used to share the MISSING-member branch and log `outcome: "failed"` — 28 such health
    events on the live instance, and under `on_failure: stop` a retirement would have become a
    daily outage of every later member."""
    from rsched import lane_runs, lanes
    from rsched.daemon.lane_runs import LaneRunManager

    server = tmp_server(tmp_path)
    for slug in ("first", "second"):
        d = server.routines_home / slug
        (d / "inbox").mkdir(parents=True, exist_ok=True)
        (d / "main.md").write_text("# main\n", encoding="utf-8")
        (d / "routine.yaml").write_text(yaml.safe_dump({
            "slug": slug, "name": slug, "enabled": True, "description": "member",
            "schedule": {"cron": "", "tz": "Europe/Berlin"}}), encoding="utf-8")
    _goal_met(server.routines_home / "first")

    lane = lanes.create(server.routines_home, name="lane",
                        members=[{"slug": "first"}, {"slug": "second"}])
    lane_runs.arm(server.routines_home, lane, default_on_failure="stop")
    fr = FakeRunner()
    mgr = LaneRunManager(server, fr)
    catalog = registry.scan(server)
    await mgr.tick(catalog)      # member 0 is retired → skipped, cursor advances
    await mgr.tick(catalog)      # member 1 fires

    rec = lane_runs.read(server.routines_home, lane["id"])
    row = rec["log"][0]
    assert row["slug"] == "first" and row["outcome"] == "skipped"    # NOT "failed"
    assert fr.fired == [("second", "lane")]                         # under on_failure=stop


# ---- a run proves it: the proposal and the two decisions that settle it -------------------------

def test_the_finish_that_proves_the_last_outcome_files_one_proposal(make_routine, scripted,
                                                                   monkeypatch):
    from rsched.engine import verifier
    from rsched.engine.runtime import run_routine
    from test_loop import TS, probe
    from test_loop import _server as loop_server

    d = make_routine(slug="goalrun")
    server = loop_server(d)
    # the verifier makes its own subcall; this test is about the PROPOSAL, not the judge
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])
    finishline.save(d, {"outcomes": [{"text": "the report is published", "judge": "run"}]},
                    now=NOW)
    scripted([probe(), {**finish(summary="Published and verified."),
                        "accounting": ["g1 met: published and the page reads back"]}])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"

    row = finishline.load(d)["outcomes"][0]
    assert row["status"] == "met" and row["evidence"] == "published and the page reads back"
    queued = pending.load_all(server.routines_home)
    assert [r["kind"] for r in queued] == ["goal-reached"]
    assert queued[0]["routine"] == "goalrun"
    assert queued[0]["fields"]["outcomes"][0]["id"] == "g1"
    assert "reached its finish line" in queued[0]["summary"]


def test_a_distance_does_not_retire_and_is_kept_for_the_next_run(make_routine, scripted,
                                                                 monkeypatch):
    from rsched.engine import verifier
    from rsched.engine.runtime import run_routine
    from test_loop import TS, probe
    from test_loop import _server as loop_server

    d = make_routine(slug="notyet")
    server = loop_server(d)
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])
    finishline.save(d, {"outcomes": [{"text": "Mark signs it off", "judge": "you"}]}, now=NOW)
    scripted([probe(), {**finish(summary="Drafted."),
                        "accounting": ["g1 distance: the budget table is still open"]}])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    row = finishline.load(d)["outcomes"][0]
    assert row["status"] == "open" and row["distance"] == "the budget table is still open"
    assert pending.load_all(server.routines_home) == []


def test_only_one_proposal_however_many_runs_follow(make_routine, tmp_path):
    """A reached finish line stays reached, so without queue-once every later run files one."""
    from types import SimpleNamespace

    from rsched.engine import goalreached

    d = make_routine(slug="once")
    server = tmp_server(tmp_path)
    _goal_met(d)
    ctx = SimpleNamespace(depth=0, server=server, routine=SimpleNamespace(
        dir=d, slug="once", name="Once"), run_id="once:1",
        transcript=SimpleNamespace(event=lambda *a, **k: None))
    assert goalreached.maybe_propose_retirement(ctx) != ""
    assert goalreached.maybe_propose_retirement(ctx) == ""
    assert len(pending.load_all(server.routines_home)) == 1


def test_approving_writes_enabled_false_through_the_one_config_writer(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="retire-me")
    _goal_met(d)
    rec = pending.queue(d.parent, kind="goal-reached", routine="retire-me", run_id="retire-me:1",
                        fields={"outcomes": []}, summary="finish line reached")

    r = c.post(f"/api/pending-creations/{rec['id']}/materialize")
    assert r.status_code == 200, r.text
    assert r.json()["retired"] == "retire-me"
    # retirement goes through the one config writer, onto the one off switch the firing path
    # reads — no second spelling beside it in the schedule
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["enabled"] is False
    assert "disabled" not in raw["schedule"]
    assert pending.load_all(d.parent) == []


def test_declining_reopens_the_finish_line_so_the_routine_runs_again(api_client, make_routine):
    """Discarding a retirement means "not yet". It HAS to change the finish line — dropping the
    record alone would leave the routine unscheduled with nothing on the page left to act on."""
    c, _tmp = api_client
    d = make_routine(slug="not-yet")
    finishline.save(d, {"outcomes": [{"text": "published", "judge": "run"}]}, now=NOW)
    finishline.record(d, {"g1": ("met", "the page reads back")}, run_id="not-yet:1", now=NOW)
    assert finishline.goal_reached(d) is True
    rec = pending.queue(d.parent, kind="goal-reached", routine="not-yet", run_id="not-yet:1",
                        fields={"outcomes": []}, summary="finish line reached")

    r = c.post(f"/api/pending-creations/{rec['id']}/discard", json={"reason": "not really"})
    assert r.status_code == 200, r.text
    assert r.json()["reopened"] == ["g1"]
    assert finishline.goal_reached(d) is False
    # the evidence the run recorded survives being overruled
    assert finishline.load(d)["outcomes"][0]["evidence"] == "the page reads back"


def test_a_card_the_calendar_raised_cannot_be_declined(api_client, make_routine):
    """Reopening changes nothing a date decides — declining would drop a card the next tick
    queues again. The date is changed instead; that save withdraws the card."""
    c, tmp = api_client
    d = make_routine(slug="dated")
    c.put("/api/routines/dated/finish-line", json={"outcomes": [], "until": "2026-01-31"})
    queued = pending.load_all(tmp / "routines")
    assert [q["routine"] for q in queued] == ["dated"]

    r = c.post(f"/api/pending-creations/{queued[0]['id']}/discard", json={"reason": ""})
    assert r.status_code == 409 and "reached by the calendar" in r.json()["detail"]
    assert len(pending.load_all(tmp / "routines")) == 1

    c.put("/api/routines/dated/finish-line", json={"outcomes": [], "until": "2999-01-31"})
    assert pending.load_all(tmp / "routines") == []
    assert finishline.goal_reached(d) is False


# ---- creation: the person's finish line is a pending change -------------------------------------

def _creation_server(tmp_path):
    from rsched.bootstrap import seed_libraries

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir(parents=True, exist_ok=True)
    server.libraries_home = tmp_path / "lib"
    seed_libraries(server.libraries_home)
    return server


def test_the_finish_line_described_at_creation_is_proposed_not_applied(tmp_path):
    """Pattern values are saved; what is specific to this routine waits under "check the changes
    i recommend." — the finish line the person described included."""
    from rsched.patterns import drafts
    from rsched.workflows.scaffold import scaffold

    server = _creation_server(tmp_path)
    d = scaffold(server, slug="applier", name="Applier",
                 instruction="Prepare and submit the grant application.",
                 workflow_slug="general-task",
                 finish_line=["run: the application is submitted", "until 2026-12-31"])
    assert finishline.load(d) == {"outcomes": [], "until": ""}
    draft = drafts.read(server.routines_home, "applier")
    assert draft is not None and draft["message"] == "check the changes i recommend."
    proposed = draft["changes"]["finish_line"]["value"]
    assert proposed["until"] == "2026-12-31"
    assert [(o["judge"], o["text"]) for o in proposed["outcomes"]] == [
        ("run", "the application is submitted")]
    assert finishline.goal_reached(d) is False


def test_a_routine_created_without_a_finish_line_runs_until_switched_off(tmp_path):
    from rsched.patterns import drafts
    from rsched.workflows.scaffold import scaffold

    server = _creation_server(tmp_path)
    d = scaffold(server, slug="watcher", name="Watcher", instruction="Watch the feed.",
                 workflow_slug="general-task")
    assert finishline.load(d) == {"outcomes": [], "until": ""}
    draft = drafts.read(server.routines_home, "watcher") or {"changes": {}}
    assert "finish_line" not in draft["changes"]
    assert finishline.goal_reached(d) is False
