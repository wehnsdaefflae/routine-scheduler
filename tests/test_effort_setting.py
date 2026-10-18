"""REASONING EFFORT is a setting of its own, beside the model it applies to.

The catalog once carried "Opus high", "Opus xhigh", … as one row per level of one model, so every
routine that wanted another level needed another row. Now a routine sets `efforts: {role: level}`
(and the server a `system_effort`), the registry applies it to whichever model serves the role, and
the model catalog carries no effort at all.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from conftest import authed_client, make_test_server, seeded_server
from rsched.cli_overrides import parse_effort_overrides
from rsched.config import EndpointConfig, ModelConfig, ServerConfig, load_routine
from rsched.daemon.runner_state import engine_cmd
from rsched.endpoints import EndpointRegistry
from rsched.paths import atomic_write_json
from rsched.web.config_fields import validate_efforts

# ---------------------------------------------------------------- the registry


@pytest.fixture
def registry():
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.models = {
        "sys": ModelConfig(name="sys", endpoint="e1", model="sys-id"),
        "prime": ModelConfig(name="prime", endpoint="e1", model="p", fallbacks=["backup"]),
        "backup": ModelConfig(name="backup", endpoint="e1", model="b"),
    }
    server.system_model = "sys"
    return EndpointRegistry(server)


def test_the_same_catalog_model_serves_every_level(registry):
    assert registry.for_model("main", {"main": "prime"}, {})[1].effort is None
    for level in ("low", "high", "max"):
        _, ref = registry.for_model("main", {"main": "prime"}, {"main": level})
        assert (ref.name, ref.effort) == ("prime", level)


def test_a_role_is_given_its_own_effort_and_not_another_roles(registry):
    models = {"main": "prime", "tool_call": "prime"}
    efforts = {"main": "xhigh", "tool_call": "low"}
    assert registry.for_model("main", models, efforts)[1].effort == "xhigh"
    assert registry.for_model("tool_call", models, efforts)[1].effort == "low"
    assert registry.for_uncensored({"uncensored": "backup"}, {"uncensored": "max"})[1].effort == "max"


def test_a_role_with_no_model_runs_the_system_model_at_its_own_or_the_system_effort(registry):
    registry.server.system_effort = "low"
    assert registry.for_model("main", {}, {})[1].effort == "low"
    assert registry.for_model("main", {}, {"main": "max"})[1].effort == "max"
    assert registry.for_system()[1].effort == "low"
    registry.server.system_effort = ""
    assert registry.for_system()[1].effort is None


def test_a_failover_chain_runs_every_member_at_the_roles_effort(registry):
    """A failover rung must not quietly change how hard the work is thought about, and no catalog
    row exists per level for it to land on."""
    chain = registry.for_model_chain("main", {"main": "prime"}, {"main": "high"})
    assert [ref.name for _, ref in chain] == ["prime", "backup"]
    assert {ref.effort for _, ref in chain} == {"high"}
    assert {ref.effort for _, ref in registry.for_model_chain("main", {"main": "prime"}, {})} \
        == {None}


# ---------------------------------------------------------------- the file


def _routine_yaml(d, **extra):
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw.update(extra)
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_efforts_load_from_routine_yaml(make_routine):
    d = make_routine(slug="thinks")
    _routine_yaml(d, efforts={"main": "high", "tool_call": "low"})
    cfg, problems = load_routine(d)
    assert not problems and cfg is not None
    assert cfg.efforts == {"main": "high", "tool_call": "low"}


def test_an_effort_the_loader_cannot_use_is_named_and_dropped(make_routine):
    d = make_routine(slug="typo")
    _routine_yaml(d, efforts={"main": "ultra", "decision": "high", "tool_call": "medium"})
    cfg, problems = load_routine(d)
    assert cfg is not None and cfg.efforts == {"tool_call": "medium"}
    assert sum("efforts." in p for p in problems) == 2
    assert any("efforts.main" in p and "low, medium, high, xhigh, max" in p for p in problems)


def test_a_catalog_row_still_carrying_effort_is_reported(tmp_path):
    from rsched.config import load_server_config

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "endpoints": {"e": {"kind": "openai", "base_url": "http://x"}},
        "models": {"old": {"endpoint": "e", "model": "m", "effort": "high"}},
        "system_model": "old", "system_effort": "turbo",
        "compaction_model": "old", "compaction_effort": "low"}), encoding="utf-8")
    server, problems = load_server_config(tmp_path / "config.yaml")
    assert any("models.old.effort" in p and "unknown key" in p for p in problems)
    assert any("system_effort" in p for p in problems) and server.system_effort == ""
    assert server.compaction_effort == "low"            # a level the loader knows is kept


# ---------------------------------------------------------------- validation


def test_validate_efforts_replaces_wholesale_and_refuses_what_it_cannot_run():
    assert validate_efforts({"main": "high", "tool_call": "", "uncensored": None}) \
        == {"main": "high"}
    assert validate_efforts(None) == {}
    for bad in ({"main": "turbo"}, {"decision": "high"}, {"subroutine": "low"}):
        with pytest.raises(HTTPException) as exc:
            validate_efforts(bad)
        assert exc.value.status_code == 400


# ---------------------------------------------------------------- the web


@pytest.fixture
def web(tmp_path, make_routine):
    make_routine(slug="webr")
    server = make_test_server(tmp_path)
    with authed_client(server) as c:
        yield c, tmp_path / "routines" / "webr", tmp_path


def test_the_routine_patch_sets_replaces_and_clears_efforts(web):
    c, d, _ = web
    assert c.get("/api/routines/webr").json()["efforts"] == {
        "main": None, "tool_call": None, "uncensored": None, "supervisor": None}
    r = c.patch("/api/routines/webr", json={"efforts": {"main": "xhigh", "tool_call": "low"}})
    assert r.status_code == 200, r.text
    assert yaml.safe_load((d / "routine.yaml").read_text())["efforts"] == {
        "main": "xhigh", "tool_call": "low"}
    detail = c.get("/api/routines/webr").json()
    assert detail["efforts"]["main"] == "xhigh"
    assert detail["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    # REPLACE wholesale: a role left out is back at the provider's default
    assert c.patch("/api/routines/webr", json={"efforts": {"main": "low"}}).status_code == 200
    assert yaml.safe_load((d / "routine.yaml").read_text())["efforts"] == {"main": "low"}
    assert c.patch("/api/routines/webr", json={"efforts": {}}).status_code == 200
    assert yaml.safe_load((d / "routine.yaml").read_text())["efforts"] == {}


def test_the_routine_patch_refuses_a_level_or_role_that_does_not_exist(web):
    c, _, _ = web
    for bad in ({"main": "turbo"}, {"decision": "high"}):
        assert c.patch("/api/routines/webr", json={"efforts": bad}).status_code == 400


def test_the_system_model_carries_its_own_effort(web):
    c, _, tmp = web
    r = c.put("/api/settings/system-model", json={"name": "m", "effort": "high"})
    assert r.status_code == 200 and r.json()["system_effort"] == "high"
    assert yaml.safe_load((tmp / "config.yaml").read_text())["system_effort"] == "high"
    assert c.get("/api/settings/endpoints").json()["system_effort"] == "high"
    assert c.get("/api/routines/webr").json()["system_effort"] == "high"
    assert c.put("/api/settings/system-model", json={"name": "m", "effort": "turbo"}) \
        .status_code == 400
    # blank = the provider's default, and the key leaves the file rather than lingering
    assert c.put("/api/settings/system-model", json={"name": "m"}).status_code == 200
    assert "system_effort" not in yaml.safe_load((tmp / "config.yaml").read_text())


def test_a_live_run_is_told_the_role_and_level_switch_independently(web):
    c, d, _ = web
    ts = "20261014-120000"
    run_dir = d / "runs" / ts
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {"run_id": f"webr:{ts}", "state": "running",
                                                "turn": 1, "pid": 1})
    ok = c.post(f"/api/runs/webr:{ts}/model", json={"kind": "main", "effort": "max"})
    assert ok.status_code == 200, ok.text
    assert "main effort → max" in ok.json()["switch"]
    switch = json.loads((run_dir / "control.json").read_text())["switch_model"]
    assert switch["main_effort"] == "max" and "main" not in switch
    assert c.post(f"/api/runs/webr:{ts}/model", json={"kind": "main"}).status_code == 400
    assert c.post(f"/api/runs/webr:{ts}/model",
                  json={"kind": "main", "effort": "turbo"}).status_code == 400


# ---------------------------------------------------------------- conversations


def test_a_conversation_is_created_with_efforts_and_edited_through_its_panel(tmp_path):
    server = seeded_server(tmp_path)
    with authed_client(server) as c:
        async def fake_fire(cfg, *, reason="x"):
            return f"{cfg.slug}:20261014-120000"

        c.app.state.runner.fire = fake_fire
        r = c.post("/api/conversations", data={
            "text": "think hard", "efforts": json.dumps({"main": "high", "tool_call": ""})})
        assert r.status_code == 200, r.text
        slug = r.json()["slug"]
        saved = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
        assert saved["efforts"] == {"main": "high"}
        detail = c.get(f"/api/conversations/{slug}").json()
        assert detail["efforts"]["main"] == "high" and "xhigh" in detail["effort_levels"]
        assert c.patch(f"/api/conversations/{slug}",
                       json={"efforts": {"tool_call": "low"}}).status_code == 200
        saved = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
        assert saved["efforts"] == {"tool_call": "low"}
        assert c.patch(f"/api/conversations/{slug}",
                       json={"efforts": {"main": "turbo"}}).status_code == 400
        assert c.post("/api/conversations", data={
            "text": "x", "efforts": '["main"]'}).status_code == 400


# ---------------------------------------------------------------- the engine process


def test_effort_overrides_parse_per_role_and_refuse_junk():
    assert parse_effort_overrides(["main=high", "tool_call=low", "main=max"]) \
        == {"main": "max", "tool_call": "low"}
    assert parse_effort_overrides(None) == {}
    for bad in ("main", "main=turbo", "decision=high", "=high"):
        with pytest.raises(SystemExit):
            parse_effort_overrides([bad])


def test_the_engine_is_told_a_trials_efforts_beside_its_models(tmp_path):
    server = SimpleNamespace(source=tmp_path / "config.yaml", routines_home=tmp_path / "routines",
                             conversations_home=tmp_path / "conv", background_home=tmp_path / "bg")
    cmd = engine_cmd(server, "slug", "20261014-120000", models={"main": "Opus"},
                     efforts={"main": "xhigh", "tool_call": "low"})
    assert cmd[cmd.index("--model") + 1] == "main=Opus"
    pairs = [cmd[i + 1] for i, word in enumerate(cmd) if word == "--effort"]
    assert pairs == ["main=xhigh", "tool_call=low"]
    assert "--effort" not in engine_cmd(server, "slug", "20261014-120000")


def test_the_engine_applies_an_effort_switch_without_touching_the_model(make_routine):
    from rsched.engine.budgets_config import Budgets
    from rsched.engine.loop import EngineLoop
    from rsched.engine.run_context import RunContext
    from rsched.engine.switches import apply_model_switch
    from rsched.engine.transcript import Transcript

    d = make_routine(slug="sw-effort")
    run_dir = d / "runs" / "20261014-120000"
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    cfg.models["main"] = "kept"
    ctx = RunContext(routine=cfg, server=ServerConfig(), registry=None, run_ts=run_dir.name,
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    loop = EngineLoop(ctx, "## Run flow", "instr")
    atomic_write_json(run_dir / "control.json", {"switch_model": {"main_effort": "max",
                                                                  "tool_call_effort": "turbo",
                                                                  "ts": "t1"}})
    apply_model_switch(loop)
    assert ctx.routine.efforts == {"main": "max"}            # a level the engine knows, only
    assert ctx.routine.models == {"main": "kept"}            # the model was never named
