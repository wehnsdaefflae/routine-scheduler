"""The gate a run writes for the NEXT fire (`rsched/gatecompose.py`), the closure-free inbox
predicate (`daemon/gate_builtin.py`), and what the gate has done (`readmodels/gate_yield.py`)."""
import json
from types import SimpleNamespace

import pytest

from rsched import gatecompose
from rsched.config import RoutineConfig
from rsched.config.routine import RunGateConfig
from rsched.daemon import gate_builtin, runner_state
from rsched.engine import gate_digest
from rsched.paths import atomic_write_json, read_json
from rsched.readmodels import gate_yield
from rsched.readmodels.surface_gate import _gate_nodes
from test_run_gate import fake_engine, finish, script, skip_body
from test_run_gate_checks import earlier, ok_run, use

FLOOR = {"kind": "max_quiet", "days": 14, "id": "heartbeat"}


def _cfg(tmp_path, *checks, enabled=True, run_written=True) -> RoutineConfig:
    return RoutineConfig(slug="r", dir=tmp_path, run_gate=RunGateConfig(
        enabled=enabled, checks=list(checks), run_written=run_written))


def _write(d, doc) -> None:
    (d / "state").mkdir(exist_ok=True)
    (d / gatecompose.FILE).write_text(json.dumps(doc) if not isinstance(doc, str) else doc)


# ── what a written gate is, and when it is set aside ─────────────────────────────────────

def test_no_file_or_an_empty_list_hands_the_fire_to_the_routines_gate(tmp_path):
    cfg = _cfg(tmp_path, FLOOR, {"kind": "weekdays", "days": [0]})
    assert gatecompose.compose(cfg) == (list(cfg.run_gate.checks), {"kind": "routine"})
    _write(tmp_path, {"why": "nothing", "checks": []})
    assert gatecompose.compose(cfg)[1] == {"kind": "routine"}


def test_a_written_gate_replaces_the_routines_checks_and_keeps_its_floor(tmp_path):
    cfg = _cfg(tmp_path, FLOOR, {"kind": "weekdays", "days": [0]})
    mine = {"kind": "dates", "from": "2026-11-14", "id": "air-date"}
    _write(tmp_path, {"why": "the queued episodes air on the 14th", "checks": [mine]})
    checks, source = gatecompose.compose(cfg)
    assert checks == [mine, {"kind": "max_quiet", "id": "backstop", "days": 14}]
    assert source["kind"] == "run" and source["checks"] == checks
    assert source["why"] == "the queued episodes air on the 14th" and source["written"]


def test_a_run_may_shorten_the_backstop_and_never_lengthen_it(tmp_path):
    cfg = _cfg(tmp_path, FLOOR)
    _write(tmp_path, {"checks": [{"kind": "max_quiet", "days": 3}]})
    assert gatecompose.compose(cfg)[0] == [{"kind": "max_quiet", "id": "backstop", "days": 3}]
    _write(tmp_path, {"checks": [{"kind": "max_quiet", "days": 30}]})
    checks, source = gatecompose.compose(cfg)
    assert checks == [FLOOR] and "never lengthen" in source["rejected"]


@pytest.mark.parametrize(("doc", "says"), [
    ("{not json", "does not parse"),
    ({"checks": "mail"}, "`checks` list"),
    ({"checks": [{"kind": "script"}]}, "routine's own predicate"),
    ({"checks": [{"kind": "mail"}]}, "mail needs 'host'"),
    ({"checks": [{"kind": "weekdays", "days": [0]}] * 13}, "at most 12"),
])
def test_a_written_gate_that_cannot_be_used_is_set_aside_with_its_reason(tmp_path, doc, says):
    cfg = _cfg(tmp_path, FLOOR)
    _write(tmp_path, doc)
    checks, source = gatecompose.compose(cfg)
    assert checks == [FLOOR] and source["kind"] == "routine"
    assert says in source["rejected"], source["rejected"]


def test_the_operators_switch_turns_written_gates_off_and_stops_asking_for_them(tmp_path):
    """`run_gate.run_written: false`: the routine's checks on every fire, the file ignored and
    not even reported as set aside, and no run asked to write one."""
    cfg = _cfg(tmp_path, FLOOR, run_written=False)
    _write(tmp_path, {"checks": [{"kind": "weekdays", "days": [1]}]})
    assert gatecompose.compose(cfg) == ([FLOOR], {"kind": "routine"})
    assert gate_digest.section(cfg) == ""
    assert RunGateConfig().run_written is True          # on unless the operator says otherwise


def test_a_routine_gate_without_a_floor_takes_no_written_gate(tmp_path):
    """The backstop is what bounds a written gate's one dangerous mistake — forgetting a source
    of work — so a gate with nothing to bound it is never replaced."""
    cfg = _cfg(tmp_path, {"kind": "weekdays", "days": [0]})
    _write(tmp_path, {"checks": [{"kind": "weekdays", "days": [1]}]})
    assert "no max_quiet backstop" in gatecompose.compose(cfg)[1]["rejected"]


def test_the_job_ledger_reads_the_checks_the_fire_actually_asked():
    mine = [{"kind": "weekdays", "days": [0], "id": "w", "job": "a"}]
    routine = [{"kind": "weekdays", "days": [1], "id": "x", "job": "b"}]
    assert gatecompose.checks_in_force(routine, {"source": {"kind": "run", "checks": mine}}) \
        == mine
    assert gatecompose.checks_in_force(routine, {"source": {"kind": "routine"}}) == routine
    assert gatecompose.checks_in_force(routine, None) == routine


# ── through the real admission path ──────────────────────────────────────────────────────

async def test_a_written_gate_skips_where_the_routines_would_have_run(setup_gate, monkeypatch):
    """The routine's script always says work; the run's own gate says when there will be some,
    and replaces the script with it — a skip the routine's gate could never produce."""
    cfg, _, runner = setup_gate
    use(cfg, {"kind": "script"}, FLOOR)
    script(cfg, 'print(\'{"version":1,"decision":"run","reason":"3 queued requests"}\')')
    ok_run(cfg, earlier())
    _write(cfg.dir, {"why": "nothing airs before the 30th",
                     "checks": [{"kind": "dates", "from": "2099-12-30", "id": "airs"}]})
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    _, run, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped"
    gate = read_json(run.run_dir / "gate.json")
    assert gate["source"]["kind"] == "run"
    assert [c["id"] for c in gate["checks"]] == ["airs", "backstop"]   # no script row


async def test_a_written_check_that_finds_work_admits_and_says_whose_it_was(setup_gate,
                                                                          monkeypatch):
    cfg, _, runner = setup_gate
    use(cfg, FLOOR)
    ok_run(cfg, earlier())
    _write(cfg.dir, {"checks": [{"kind": "dates", "from": "2026-01-01", "id": "due",
                                 "job": "a"}]})
    marker = fake_engine(monkeypatch, cfg)
    _, run, _ = await finish(runner, cfg)
    assert marker.exists()
    gate = read_json(run.run_dir / "gate.json")
    assert gate["decision"] == "run" and gate["source"]["kind"] == "run"
    assert gatecompose.checks_in_force([FLOOR], gate)[0]["job"] == "a"


async def test_an_ungranted_path_sets_the_written_gate_aside_and_never_fails_the_fire(
        setup_gate, monkeypatch, tmp_path):
    cfg, _, runner = setup_gate
    use(cfg, FLOOR)
    ok_run(cfg, earlier())
    (tmp_path / "elsewhere").mkdir()
    _write(cfg.dir, {"checks": [{"kind": "files_changed", "paths": [str(tmp_path / "elsewhere")]}]})
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    _, run, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped"          # the routine's own floor, quiet, decided it
    source = read_json(run.run_dir / "gate.json")["source"]
    assert source["kind"] == "routine" and "outside this routine's" in source["rejected"]


async def test_a_closure_alone_admits_no_fire(setup_gate, monkeypatch):
    """A `closes: true` reply asks nothing of the routine; it waits in the inbox for the next
    admitted run instead of buying one (10 of 43 inbox admissions up to 2026-11-08)."""
    cfg, _, runner = setup_gate
    script(cfg, skip_body())
    (cfg.dir / "inbox").mkdir()
    closure = cfg.dir / "inbox" / "msg-rep-R9.json"
    closure.write_text(json.dumps({"text": "settled", "via": "report", "closes": True}))
    monkeypatch.setattr(runner_state, "engine_cmd", lambda *a, **k: pytest.fail("engine boot"))
    _, _, st = await finish(runner, cfg)
    assert st["outcome"] == "skipped" and closure.exists()      # kept for the next run


def test_an_unparseable_message_is_still_freight(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "msg-x.json").write_text("{")
    assert gate_builtin.pending_inbox(tmp_path)


# ── what the gate has done, and who is told ──────────────────────────────────────────────

def _fire(d, ts, decision, checks, source="routine"):
    rd = d / "runs" / ts
    rd.mkdir(parents=True)
    atomic_write_json(rd / "gate.json", {"decision": decision, "reason": "r", "checks": checks,
                                         **({"source": {"kind": source}} if checks else {})})


def _always(n, d):
    for i in range(n):
        _fire(d, f"2026110{i}-060000", "run", [
            {"id": "queue", "kind": "script", "work": True, "reason": "3 queued"},
            {"id": "heartbeat", "kind": "max_quiet", "work": False, "reason": "fresh"}])


def test_a_check_that_always_finds_work_is_named_and_the_backstop_is_not(tmp_path):
    _always(gate_yield.MIN_ASKS, tmp_path)
    _fire(tmp_path, "20261109-060000", "run", [])                   # a built-in reason
    _fire(tmp_path, "20261110-060000", "skip", [
        {"id": "airs", "kind": "dates", "work": False, "reason": "not yet"}], source="run")
    r = gate_yield.reading(tmp_path)
    assert (r["judged"], r["skipped"], r["before_checks"]) == (7, 1, 1)
    assert r["run_written"] == {"fires": 1, "skipped": 1}
    assert [c["id"] for c in r["never_idle"]] == ["queue"]


def test_fewer_asks_than_the_minimum_are_an_anecdote(tmp_path):
    _always(gate_yield.MIN_ASKS - 1, tmp_path)
    assert gate_yield.reading(tmp_path)["never_idle"] == []


def test_the_setup_surface_names_a_check_that_never_let_a_fire_skip(tmp_path):
    _always(gate_yield.MIN_ASKS, tmp_path)
    server = SimpleNamespace(routines_home=tmp_path.parent, conversations_home=None)
    [node] = _gate_nodes(server, _cfg(tmp_path, FLOOR))
    assert node["id"] == "gate:queue" and node["severity"] == "note"
    assert node["fix"] == {"kind": "edit_gate", "check": "queue"}
    assert _gate_nodes(server, _cfg(tmp_path, FLOOR, enabled=False)) == []


def test_the_run_is_asked_for_the_next_fires_gate_only_where_it_can_take_effect(tmp_path):
    _always(gate_yield.MIN_ASKS, tmp_path)
    block = gate_digest.section(_cfg(tmp_path, FLOOR, {"kind": "script"}))
    assert block.startswith("NEXT FIRE'S GATE (state/next-gate.json")
    assert "max_quiet backstop of 14 days" in block
    assert "queue (script) answered work on all 5 asks" in block
    assert "  - mail(host*," in block and "  - script(" not in block
    assert gate_digest.section(_cfg(tmp_path, {"kind": "script"})) == ""      # no floor
    assert gate_digest.section(_cfg(tmp_path, FLOOR, enabled=False)) == ""
    _write(tmp_path, {"checks": [{"kind": "max_quiet", "days": 99}]})
    assert "the written one is set aside: max_quiet 99 days" in gate_digest.section(
        _cfg(tmp_path, FLOOR))
