"""Declarative checks through the real admission path: jail, baseline, fingerprints."""
import pytest

from rsched.config.routine import RunGateConfig
from rsched.daemon import runner_state
from rsched.paths import atomic_write_json, read_json
from test_run_gate import fake_engine, finish, script, skip_body
from test_run_gate import setup_gate as gate_fixture

setup_gate = gate_fixture


def use(cfg, *checks, timeout=60):
    cfg.run_gate = RunGateConfig(enabled=True, timeout_s=timeout, checks=list(checks))


def earlier() -> str:
    """A run id one minute ago — never the same second as the fire under test."""
    from datetime import UTC, datetime, timedelta

    from rsched.ids import run_ts
    return run_ts(datetime.now(UTC) - timedelta(minutes=1))


def ok_run(cfg, ts, fingerprints=None):
    rd = cfg.dir / "runs" / ts
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": f"{cfg.slug}:{ts}", "state": "finished",
                                           "outcome": "ok"})
    if fingerprints is not None:
        atomic_write_json(rd / "gate.json", {"fingerprints": fingerprints})


async def test_a_quiet_backstop_skips_after_a_fresh_ok_run(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30})
    ok_run(cfg, earlier())
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    _, run, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped"
    gate = read_json(run.run_dir / "gate.json")
    assert gate["decision"] == "skip" and gate["checks"][0]["kind"] == "max_quiet"


async def test_no_baseline_admits(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    assert read_json(run.run_dir / "gate.json")["decision"] == "run"


async def test_a_partial_last_run_admits_without_asking_the_checks(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30})
    rd = cfg.dir / "runs" / "20260101-000000"
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": f"{cfg.slug}:20260101-000000",
                                           "state": "finished", "outcome": "partial"})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    assert "left work behind" in read_json(run.run_dir / "gate.json")["reason"]


async def test_fingerprints_are_recorded_for_the_next_fire(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    feed = cfg.dir / "state" / "feed.xml"
    feed.parent.mkdir()
    feed.write_text("<rss><item><guid>a</guid></item></rss>")
    use(cfg, {"kind": "url_changed", "url": feed.as_uri(), "select": "feed", "id": "feed"})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    prints = read_json(run.run_dir / "gate.json")["fingerprints"]
    assert set(prints) == {"feed"}


async def test_a_check_outside_the_roots_fails_closed(setup_gate, tmp_path):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "files_changed", "paths": [str(tmp_path / "elsewhere")]})
    (tmp_path / "elsewhere").mkdir()
    ok_run(cfg, earlier())
    _, run, st = await finish(runner, cfg)
    assert st["state"] == "failed"
    assert "outside this routine's filesystem roots" in (
        run.run_dir / "gate-stderr.txt").read_text()


async def test_checks_and_a_script_must_both_agree_to_skip(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30}, {"kind": "script"})
    ok_run(cfg, earlier())
    script(cfg, skip_body().replace("skip", "run"))
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    kinds = [c["kind"] for c in read_json(run.run_dir / "gate.json")["checks"]]
    assert kinds == ["max_quiet", "script"]


def test_the_vocabulary_endpoint_serves_the_kit(api_client):
    c, _ = api_client
    kinds = c.get("/api/gate/kinds").json()["kinds"]
    assert set(kinds) == set(__import__("rsched.gatekit", fromlist=["KINDS"]).KINDS)
    assert kinds["max_quiet"]["params"]["days"]["required"] is True


def test_testing_the_gate_reports_without_running(api_client, make_routine):
    c, tmp = api_client
    make_routine("tested")
    out = c.post("/api/routines/tested/gate/test",
                 json={"run_gate": {"checks": [{"kind": "max_quiet", "days": 5}]}}).json()
    assert out["decision"] == "run"                      # no ok run yet: nothing to compare
    assert out["checks"][0]["kind"] == "max_quiet"
    assert not (tmp / "routines/tested/runs").exists()   # a test is never a run


async def test_a_waiting_answer_is_work_for_a_scheduled_fire(setup_gate, monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30})
    ok_run(cfg, earlier())
    (cfg.dir / "inbox").mkdir()
    (cfg.dir / "questions" / "pending").mkdir(parents=True)
    atomic_write_json(cfg.dir / "inbox" / "answer-q-20260929-1.json",
                      {"qid": "q-20260929-1", "text": "yes"})
    atomic_write_json(cfg.dir / "questions" / "pending" / "q-20260929-1.json",
                      {"question": "may I?"})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    assert "answer" in read_json(run.run_dir / "gate.json")["reason"]


async def test_a_resume_keeps_the_admission_record(setup_gate, monkeypatch):
    from rsched.daemon import run_gate
    cfg, _, runner = setup_gate
    run = type("R", (), {"user_cancel": False, "cancelled": False, "run_id": "x:1",
                         "run_dir": cfg.dir})()
    atomic_write_json(cfg.dir / "gate.json", {"decision": "run", "reason": "lane fire"})
    assert await run_gate.admit(run, cfg, runner.server, "schedule", resume=True)
    assert read_json(cfg.dir / "gate.json")["reason"] == "lane fire"


async def test_a_note_waiting_in_a_shared_store_admits_the_fire(setup_gate, monkeypatch):
    """A teammate's note is delivered at the next boot, so a gate that skipped would hold the
    hand-off until something else admitted a run."""
    from rsched import sharedstores
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "max_quiet", "days": 30})
    ok_run(cfg, earlier())
    store = sharedstores.stores_home(runner.server.routines_home) / "grp-team"
    notes = sharedstores.notes_dir(store, cfg.slug)
    notes.mkdir(parents=True)
    cfg.fs_write_roots = [str(store)]
    atomic_write_json(notes / "note-1.json", {"from": "sibling", "ts": "t", "text": "staged"})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    assert "note" in read_json(run.run_dir / "gate.json")["reason"]


def test_a_stray_answer_whose_question_is_gone_is_not_work(tmp_path):
    from rsched.daemon import gate_prepare
    (tmp_path / "inbox").mkdir()
    atomic_write_json(tmp_path / "inbox" / "answer-q-1.json", {"qid": "q-1", "text": "x"})
    assert not gate_prepare.pending_answers(tmp_path)
    (tmp_path / "questions" / "pending").mkdir(parents=True)
    atomic_write_json(tmp_path / "questions" / "pending" / "q-1.json", {"question": "?"})
    assert gate_prepare.pending_answers(tmp_path)


def test_a_changed_recipe_since_the_last_ok_run_is_work(tmp_path):
    import os
    import time

    from rsched.daemon import gate_prepare
    (tmp_path / "main.md").write_text("recipe")
    past = time.time() - 3600
    os.utime(tmp_path / "main.md", (past, past))
    since = __import__("datetime").datetime.fromtimestamp(past + 60).astimezone().isoformat()
    assert gate_prepare.changed_since(tmp_path, since) == ""
    (tmp_path / "main.md").write_text("recipe, revised")
    assert gate_prepare.changed_since(tmp_path, since) == "main.md"
