"""D141 — the run gate is reachable from the console, beside the schedule.

The gate itself (F457) has been built and wired into admission for some time:
`daemon/run_gate.admit` is consulted in `Runner._supervise` before the engine subprocess is
created, and `PATCH /api/routines/{slug}` has accepted `run_gate` all along. What did not
exist was any way to SEE or SET it without hand-editing `routine.yaml` — so a feature built
to save budget was reachable only by someone who already knew it was there, and a routine
silently not running looked exactly like a routine that was broken.

The operator chose where it goes (2026-09-21): *"On the config page beside the schedule"* —
a gate decides WHETHER a scheduled fire becomes a run, so it belongs with WHEN it fires,
under the Schedule section's own save.

These pin the contract that control needs: the current state is readable, a change persists,
and the same save can carry the schedule and the gate together without either clobbering
the other.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rsched.paths import read_yaml


@pytest.fixture
def client(tmp_path, make_routine):
    from rsched.config import ServerConfig
    from rsched.web.app import create_app

    # make_routine writes into tmp_path/routines/<slug> itself and returns that dir
    home = make_routine(slug="gated").parent
    cfg = ServerConfig(token="tok", routines_home=home,
                       conversations_home=tmp_path / "conversations",
                       background_home=tmp_path / "background",
                       libraries_home=tmp_path / "library")
    for d in (cfg.conversations_home, cfg.background_home, cfg.libraries_home):
        d.mkdir(parents=True, exist_ok=True)
    app = create_app(cfg, with_scheduler=False)
    return TestClient(app), cfg


AUTH = {"Authorization": "Bearer tok"}


def test_the_detail_payload_carries_the_gate_so_the_page_can_render_it(client):
    """Without this the control has nothing to show, and would render every routine as
    ungated whatever its config says."""
    c, _cfg = client
    d = c.get("/api/routines/gated", headers=AUTH).json()
    assert "run_gate" in d, "the config page cannot render a field the API never returns"
    assert d["run_gate"] == {"enabled": False, "timeout_s": 30}   # the documented default


def test_enabling_the_gate_from_the_page_persists_it(client):
    c, cfg = client
    r = c.patch("/api/routines/gated", headers=AUTH,
                json={"run_gate": {"enabled": True, "timeout_s": 45}})
    assert r.status_code == 200, r.text
    assert "run_gate" in r.json()["updated"], r.json()

    stored = read_yaml(cfg.routines_home / "gated" / "routine.yaml", {})
    assert stored["run_gate"] == {"enabled": True, "timeout_s": 45}
    assert c.get("/api/routines/gated", headers=AUTH).json()["run_gate"]["enabled"] is True


def test_one_save_carries_the_schedule_and_the_gate_without_clobbering_either(client):
    """The operator put the gate INSIDE the Schedule section, so its save sends both. Each
    must land — a partial apply here is how a reader ends up trusting a switch that did
    nothing."""
    c, _cfg = client
    r = c.patch("/api/routines/gated", headers=AUTH,
                json={"schedule": {"friendly": {"frequency": "daily", "hour": 6, "minute": 30}},
                      "run_gate": {"enabled": True, "timeout_s": 20}})
    assert r.status_code == 200, r.text
    updated = r.json()["updated"]
    assert "run_gate" in updated and "schedule" in updated, updated

    d = c.get("/api/routines/gated", headers=AUTH).json()
    assert d["run_gate"] == {"enabled": True, "timeout_s": 20}
    assert d["schedule_friendly"]["frequency"] == "daily"


def test_a_gate_timeout_outside_the_allowed_range_is_refused(client):
    """RunGateConfig bounds the timeout 1..300; the page must not be able to store a value
    the daemon would then reject at admission time."""
    c, _cfg = client
    assert c.patch("/api/routines/gated", headers=AUTH,
                   json={"run_gate": {"enabled": True, "timeout_s": 0}}).status_code == 422
    assert c.patch("/api/routines/gated", headers=AUTH,
                   json={"run_gate": {"enabled": True, "timeout_s": 9999}}).status_code == 422
