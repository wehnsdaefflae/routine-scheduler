"""Config loading: the deployed config.yaml keys keep loading unchanged, and both loaders
degrade per key — every invalid value becomes a problem line plus its default, never a
crash or a discarded config."""

from __future__ import annotations

import yaml

from rsched.config import (DEFAULT_BUDGETS, DEFAULT_PERMISSIONS, EndpointConfig, ModelRef,
                           RoutineConfig, ServerConfig, load_routine, load_server_config)

# ---------------------------------------------------------------- server config


def _load_server(tmp_path, data: dict):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_server_config(p)


def test_deployed_config_keys_load_exactly(tmp_path):
    """Precisely the keys a deployed ~/.config/routine-scheduler/config.yaml uses —
    this shape MUST keep loading with zero problems."""
    server, problems = _load_server(tmp_path, {
        "bind": "0.0.0.0",
        "port": 9000,
        "token": "s3cret",
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "libs"),
        "libraries_remote": "git@github.com:me/libs.git",
        "max_concurrent_runs": 3,
        "registry_rescan_s": 15,
        "endpoints": {
            "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                           "api_key": "sk-or-xyz", "key_var": "OPENROUTER_KEY",
                           "schema_mode": "json_object", "context_chars": 180_000},
            "cc": {"kind": "claude-cli"},
        },
        "system_model": {"endpoint": "openrouter", "model": "deepseek/deepseek-chat"},
        "library_sync": {"enabled": True, "cron": "0 6 * * *", "tz": "Europe/Berlin"},
    })
    assert problems == []
    assert (server.bind, server.port, server.token) == ("0.0.0.0", 9000, "s3cret")
    assert server.routines_home == tmp_path / "routines"
    assert server.libraries_home == tmp_path / "libs"
    assert server.libraries_remote == "git@github.com:me/libs.git"
    assert (server.max_concurrent_runs, server.registry_rescan_s) == (3, 15)
    ep = server.endpoints["openrouter"]
    assert ep.name == "openrouter" and ep.kind == "openai"
    assert ep.base_url == "https://openrouter.ai/api/v1" and ep.api_key == "sk-or-xyz"
    assert ep.key_var == "OPENROUTER_KEY" and ep.schema_mode == "json_object"
    assert ep.context_chars == 180_000
    assert server.endpoints["cc"].kind == "claude-cli"
    assert server.system_model == ModelRef("openrouter", "deepseek/deepseek-chat")
    # derived properties hang off libraries_home
    assert server.library_home == server.libraries_home == server.utils_home
    assert server.traits_home == server.libraries_home / "traits"
    assert server.permissions_home == server.libraries_home / "permissions"
    assert server.library_sync.enabled is True and server.library_sync.cron == "0 6 * * *"


def test_library_sync_defaults_and_bad_cron_degrades(tmp_path):
    server, problems = _load_server(tmp_path, {"bind": "127.0.0.1"})
    assert problems == []
    assert server.library_sync.enabled is False
    assert server.library_sync.cron == "0 6 * * *"
    server, problems = _load_server(tmp_path, {"library_sync": {"enabled": True,
                                                                "cron": "not a cron"}})
    assert any("library_sync.cron" in pr for pr in problems)
    assert server.library_sync.cron == "0 6 * * *"      # bad key dropped, default survives
    assert server.library_sync.enabled is True          # the valid sibling key still loads


def test_server_defaults_and_missing_file(tmp_path):
    server, problems = load_server_config(tmp_path / "nope.yaml")
    assert len(problems) == 1 and "not found" in problems[0]
    assert server.port == 8321 and server.endpoints == {} and server.system_model is None


def test_server_bad_keys_degrade_per_key(tmp_path):
    """One bad key never poisons the rest: it is reported and falls back to its default."""
    server, problems = _load_server(tmp_path, {
        "port": "not-a-port", "bind": "10.0.0.1",
        "endpoints": {"good": {"kind": "openai", "base_url": "http://x"},
                      "bad": {"kind": "smtp"}},
        "system_model": {"endpoint": "good", "model": "m"},
    })
    text = " | ".join(problems)
    assert "port" in text and "endpoints.bad.kind" in text
    assert server.port == 8321                      # bad key → default
    assert server.bind == "10.0.0.1"                # good key survives
    assert set(server.endpoints) == {"good"}        # bad endpoint skipped, good one kept
    assert server.system_model == ModelRef("good", "m")


def test_server_unknown_system_model_endpoint_flagged(tmp_path):
    server, problems = _load_server(tmp_path, {
        "system_model": {"endpoint": "ghost", "model": "m"}})
    assert any("system_model" in p and "ghost" in p for p in problems)
    assert server.system_model == ModelRef("ghost", "m")  # kept — the UI shows the problem


def test_server_config_direct_construction_for_tests():
    """The fixture pattern all engine tests rely on: bare construction + assignment."""
    s = ServerConfig()
    s.system_model = ModelRef("scripted", "test-model")
    s.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    assert s.system_model.model == "test-model" and s.endpoints["e1"].context_chars == 100_000


# ---------------------------------------------------------------- routine.yaml


def _mk_routine(tmp_path, data: dict, slug="testr", files=True):
    d = tmp_path / slug
    d.mkdir()
    (d / "routine.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if files:
        (d / "main.md").write_text("## Run flow\n", encoding="utf-8")
        (d / "instruction.md").write_text("do it", encoding="utf-8")
    return d


def test_routine_full_shape_loads(tmp_path):
    d = _mk_routine(tmp_path, {
        "slug": "testr", "name": "Test", "description": "A test routine.",
        "enabled": True, "tags": ["meta", " demo "],
        "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "run_once"},
        "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
        "models": {"main": {"endpoint": "e", "model": "m", "effort": "high"}},
        "budgets": {"max_turns": 10},
        "permissions": ["util-authoring"],
        "fs_read_roots": ["~/data"],
        "retention": {"keep_runs": 5},
    })
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.slug == "testr" and cfg.name == "Test" and cfg.tags == ["meta", "demo"]
    assert (cfg.cron, cfg.tz, cfg.catchup) == ("0 7 * * 1", "Europe/Berlin", "run_once")
    assert (cfg.workflow_slug, cfg.workflow_commit) == ("test-flow", "abc123")
    assert cfg.models["main"] == ModelRef("e", "m", "high")
    assert cfg.budgets == {**DEFAULT_BUDGETS, "max_turns": 10}  # merged over defaults
    assert cfg.permissions == ["util-authoring"] and cfg.keep_runs == 5
    assert cfg.fs_read_roots[0].name == "data" and cfg.fs_read_roots[0].is_absolute()


def test_routine_minimal_gets_defaults(tmp_path):
    d = _mk_routine(tmp_path, {"description": "Minimal."})
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.slug == "testr" and cfg.name == "testr" and cfg.enabled is True
    assert cfg.budgets == DEFAULT_BUDGETS and cfg.permissions == DEFAULT_PERMISSIONS
    assert "util-authoring" in cfg.permissions        # write_util grant is in the default set
    assert cfg.catchup == "skip" and cfg.keep_runs == 30


def test_routine_bad_values_reported_and_defaulted(tmp_path):
    d = _mk_routine(tmp_path, {
        "description": "Bad bits.",
        "schedule": {"cron": "not a cron", "catchup": "sometimes"},
        "budgets": {"max_turns": "many", "max_lightyears": 3},
        "models": {"main": {"endpoint": "e"}, "sidekick": {"endpoint": "e", "model": "m"}},
    })
    cfg, problems = load_routine(d)
    text = " | ".join(problems)
    assert "schedule.cron" in text and "schedule.catchup" in text
    assert "budgets.max_turns" in text and "budgets.max_lightyears: unknown budget" in text
    assert "models.main" in text and "models.sidekick: unknown model kind" in text
    assert cfg.cron == "" and cfg.catchup == "skip"                # invalid → defaults
    assert cfg.budgets == DEFAULT_BUDGETS and cfg.models == {}
    # none of these problems is fatal to runtime (its gate greps for "missing")
    assert not any("missing" in p for p in problems)


def test_routine_structural_problems(tmp_path):
    d = _mk_routine(tmp_path, {"slug": "Wrong_Name", "description": "x"}, files=False)
    cfg, problems = load_routine(d)
    text = " | ".join(problems)
    assert "not kebab-case" in text and "does not match directory name" in text
    assert "no main.md" in text and "instruction.md missing" in text
    assert cfg is not None  # best-effort config still comes back

    cfg2, problems2 = load_routine(tmp_path)  # no routine.yaml at all
    assert cfg2 is None and len(problems2) == 1


def test_routine_empty_description_flagged(tmp_path):
    d = _mk_routine(tmp_path, {"schedule": {"cron": "0 7 * * 1"}})
    cfg, problems = load_routine(d)
    assert any("description is empty" in p for p in problems)
    assert cfg.cron == "0 7 * * 1"


def test_routine_explicit_empty_permissions_wins(tmp_path):
    d = _mk_routine(tmp_path, {"description": "x", "permissions": []})
    cfg, problems = load_routine(d)
    assert cfg.permissions == [] and problems == []


def test_routine_legacy_ask_timeout_h_converts_to_minutes(tmp_path):
    """routine.yaml written before the timeout moved to minutes keeps its meaning:
    ask_timeout_h is converted (x60), never dropped as an unknown budget."""
    d = _mk_routine(tmp_path, {"description": "Legacy timeout.",
                               "budgets": {"ask_timeout_h": 2}})
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.budgets["ask_timeout_min"] == 120
    assert "ask_timeout_h" not in cfg.budgets


def test_routine_explicit_ask_timeout_min_beats_legacy(tmp_path):
    """When both keys appear, the new one wins; the legacy key is discarded silently."""
    d = _mk_routine(tmp_path, {"description": "Both timeouts.",
                               "budgets": {"ask_timeout_h": 2, "ask_timeout_min": 7}})
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.budgets["ask_timeout_min"] == 7


def test_bare_serverconfig_is_hermetic_under_pytest(tmp_path):
    """Regression (health-event leak): a bare ServerConfig() built inside a test must not
    point at the REAL ~/routines — otherwise fixture runs append run_failed noise into the
    live health-events.jsonl on every pytest invocation (the _hermetic_home autouse
    fixture redirects ~ into tmp)."""
    from pathlib import Path
    s = ServerConfig()
    assert str(Path.home()) not in str(s.routines_home)
    assert str(Path.home()) not in str(s.libraries_home)
