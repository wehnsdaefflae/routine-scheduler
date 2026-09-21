"""Strict admission config and partial-patch regression coverage."""
import pytest

from rsched.config import load_routine
from rsched.paths import atomic_write_json, atomic_write_yaml, read_json, read_yaml


@pytest.mark.parametrize("gate", [None, [], True, "yes", {"wat": 1},
    *({"enabled": v} for v in [None, 0, 1, "true"]),
    *({"timeout_s": v} for v in [None, True, 1.5, "2", -1, 0, 301])])
def test_invalid_gate_never_recovers(make_routine, gate):
    d = make_routine()
    atomic_write_yaml(d / "routine.yaml", {"run_gate": gate})
    cfg, problems = load_routine(d)
    assert cfg is None
    assert any("run_gate" in p for p in problems)


@pytest.mark.parametrize("timeout", [1, 12, 300])
def test_valid_gate_survives_scalar_recovery(make_routine, timeout):
    d = make_routine()
    atomic_write_yaml(d / "routine.yaml", {
        "run_gate": {"enabled": True, "timeout_s": timeout}, "enabled": "junk"})
    cfg, problems = load_routine(d)
    assert problems and cfg is not None
    assert cfg.run_gate.enabled and cfg.run_gate.timeout_s == timeout


def test_enabled_gate_rejects_exhausted_recovery(make_routine):
    d = make_routine()
    atomic_write_yaml(d / "routine.yaml", {
        "run_gate": {"enabled": True, "timeout_s": 12}, "machines": [{}]})
    cfg, problems = load_routine(d)
    assert cfg is None
    assert any("whole-config fallback" in p for p in problems)


def test_absent_defaults_off(make_routine):
    cfg, _ = load_routine(make_routine())
    assert cfg is not None
    assert cfg.run_gate.model_dump() == {"enabled": False, "timeout_s": 30}


def test_partial_patch_signal_matches_saved(api_client, make_routine):
    c, _ = api_client
    d = make_routine(slug="gate-patch")
    path = d / "routine.yaml"
    assert c.patch("/api/routines/gate-patch", json={"name": "other"}).status_code == 200
    assert "run_gate" not in read_yaml(path)
    assert c.patch("/api/routines/gate-patch", json={
        "run_gate": {"enabled": True, "timeout_s": 12}}).status_code == 200
    rd = d / "runs" / "20260921-010000"
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": "gate-patch:20260921-010000",
                                         "state": "running", "turn": 1})
    for partial in [{"timeout_s": 15}, {"enabled": False}, {}, {"enabled": True}]:
        before = read_yaml(path)["run_gate"]
        response = c.patch("/api/routines/gate-patch", json={"run_gate": partial})
        assert response.status_code == 200
        saved = read_yaml(path)["run_gate"]
        assert saved == {**before, **partial}
        signal = read_json(rd / "control.json")["config_change"]["values"]["run_gate"]
        assert signal == partial
        assert all(saved[k] == v for k, v in signal.items())
    before = path.read_text()
    assert c.patch("/api/routines/gate-patch", json={"run_gate": None}).status_code == 200
    assert path.read_text() == before
    for gate in [{"enabled": "true"}, {"timeout_s": True}, {"timeout_s": 301}, [],
                 {"enabled": False, "timeout_s": 0}, {"unknown": 1}]:
        assert c.patch("/api/routines/gate-patch", json={"run_gate": gate}).status_code == 422
        assert path.read_text() == before
