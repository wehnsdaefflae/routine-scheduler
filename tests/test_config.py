"""Config loading: the deployed config.yaml keys keep loading unchanged, and both loaders
degrade per key — every invalid value becomes a problem line plus its default, never a
crash or a discarded config."""

from __future__ import annotations

import yaml

from rsched.config import (
    DEFAULT_BUDGETS,
    DEFAULT_PERMISSIONS,
    EndpointConfig,
    ModelConfig,
    ServerConfig,
    load_routine,
    load_server_config,
)

# ---------------------------------------------------------------- server config


def _load_server(tmp_path, data: dict):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_server_config(p)


def test_deployed_config_keys_load_exactly(tmp_path):
    """Precisely the keys a deployed ~/.config/routine-scheduler/config.yaml uses —
    this shape MUST keep loading with zero problems."""
    server, problems = _load_server(tmp_path, {
        "bind": "0.0.0.0",  # noqa: S104 — fixture value, nothing binds in tests
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
        "models": {"ds": {"endpoint": "openrouter", "model": "deepseek/deepseek-chat",
                          "multimodal": False, "context_chars": 200_000, "effort": "high"}},
        "system_model": "ds",
        "library_sync": {"enabled": True, "cron": "0 6 * * *", "tz": "Europe/Berlin"},
    })
    assert problems == []
    assert (server.bind, server.port, server.token) == ("0.0.0.0", 9000, "s3cret")  # noqa: S104
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
    mc = server.models["ds"]
    assert mc.name == "ds" and mc.endpoint == "openrouter" and mc.model == "deepseek/deepseek-chat"
    assert mc.multimodal is False and mc.context_chars == 200_000 and mc.effort == "high"
    assert server.system_model == "ds"
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
    assert server.port == 8321 and server.endpoints == {}
    assert server.models == {} and server.system_model == ""


def test_server_bad_keys_degrade_per_key(tmp_path):
    """One bad key never poisons the rest: it is reported and falls back to its default."""
    server, problems = _load_server(tmp_path, {
        "port": "not-a-port", "bind": "10.0.0.1",
        "endpoints": {"good": {"kind": "openai", "base_url": "http://x"},
                      "bad": {"kind": "smtp"}},
        "models": {"m": {"endpoint": "good", "model": "m"}},
        "system_model": "m",
    })
    text = " | ".join(problems)
    assert "port" in text and "endpoints.bad.kind" in text
    assert server.port == 8321                      # bad key → default
    assert server.bind == "10.0.0.1"                # good key survives
    assert set(server.endpoints) == {"good"}        # bad endpoint skipped, good one kept
    assert server.system_model == "m" and server.models["m"].endpoint == "good"


def test_server_unknown_system_model_and_model_endpoint_flagged(tmp_path):
    # system_model must name a catalog model; a catalog model's endpoint must be configured
    server, problems = _load_server(tmp_path, {
        "system_model": "ghost",
        "models": {"orphan": {"endpoint": "nope", "model": "m"}}})
    assert any("system_model" in p and "ghost" in p for p in problems)
    assert any("models.orphan" in p and "nope" in p for p in problems)
    assert server.system_model == "ghost"   # kept — the UI shows the problem


def test_server_unknown_endpoint_and_model_keys_flagged(tmp_path):
    """extra="ignore" drops unknown keys silently — the loader surfaces each mistyped
    endpoint/model key as a problem line (a warning; the entry still loads)."""
    server, problems = _load_server(tmp_path, {
        "endpoints": {"e": {"kind": "openai", "base_url": "http://x", "multimodal": True}},
        "models": {"m": {"endpoint": "e", "model": "id", "contxt_chars": 5}},
        "system_model": "m",
    })
    text = " | ".join(problems)
    assert "endpoints.e.multimodal: unknown key" in text
    assert "models.m.contxt_chars: unknown key" in text
    assert set(server.endpoints) == {"e"} and set(server.models) == {"m"}  # warn, never fail


def test_endpoint_key_var_defaults_per_kind():
    """key_var left unset falls to the KIND's own key variable — an openai endpoint must
    never default to the Anthropic key; claude-cli auths via the subscription token."""
    assert EndpointConfig(name="a", kind="anthropic").key_var == "ANTHROPIC_API_KEY"
    assert EndpointConfig(name="o", kind="openai", base_url="http://x").key_var == "OPENAI_API_KEY"
    assert EndpointConfig(name="c", kind="claude-cli").key_var == ""
    # an explicit key_var always wins over the kind default
    ep = EndpointConfig(name="o2", kind="openai", base_url="http://x", key_var="OPENROUTER_KEY")
    assert ep.key_var == "OPENROUTER_KEY"


def test_server_config_direct_construction_for_tests():
    """The fixture pattern all engine tests rely on: bare construction + assignment."""
    s = ServerConfig()
    s.models = {"sys": ModelConfig(name="sys", endpoint="e1", model="test-model")}
    s.system_model = "sys"
    s.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    assert s.system_model == "sys" and s.models["sys"].model == "test-model"
    assert s.endpoints["e1"].context_chars == 100_000


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
        "models": {"main": "gpt"},
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
    assert cfg.models["main"] == "gpt"      # role → catalog model NAME
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
        # main is a dict (invalid: model roles are catalog NAMES → per-key drop); sidekick is
        # a valid string but an unknown role kind (deleted with a problem line).
        "models": {"main": {"endpoint": "e"}, "sidekick": "x"},
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


def test_routine_accepts_uncensored_model_role(tmp_path):
    from rsched.config import MODEL_KINDS
    assert "uncensored" in MODEL_KINDS      # the optional 4th role
    d = _mk_routine(tmp_path, {
        "description": "Has an uncensored referral target.",
        "models": {"tool_call": "normal", "uncensored": "abliterated"},
    })
    cfg, problems = load_routine(d)
    assert not any("uncensored" in p for p in problems)
    assert cfg.models["uncensored"] == "abliterated"


def test_routine_structural_problems(tmp_path):
    d = _mk_routine(tmp_path, {"slug": "Wrong_Name", "description": "x"}, files=False)
    cfg, problems = load_routine(d)
    text = " | ".join(problems)
    assert "not kebab-case" in text and "does not match directory name" in text
    assert "no main.md" in text   # instruction.md is a transient seed, no longer required
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


def test_routine_null_roots_and_models_get_their_own_defaults(tmp_path):
    """A bare `fs_read_roots:` / `fs_write_roots:` / `models:` key (YAML null) reads as the
    FIELD'S OWN empty default — regression: the list fields used to borrow the models
    field's {} and fail list validation."""
    d = _mk_routine(tmp_path, {"description": "Nulls.", "fs_read_roots": None,
                               "fs_write_roots": None, "models": None})
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.fs_read_roots == [] and cfg.fs_write_roots == [] and cfg.models == {}


def test_bare_serverconfig_is_hermetic_under_pytest(tmp_path):
    """Regression (health-event leak): a bare ServerConfig() built inside a test must not
    point at the REAL ~/routines — otherwise fixture runs append run_failed noise into the
    live health-events.jsonl on every pytest invocation (the _hermetic_home autouse
    fixture redirects ~ into tmp)."""
    from pathlib import Path
    s = ServerConfig()
    assert str(Path.home()) not in str(s.routines_home)
    assert str(Path.home()) not in str(s.libraries_home)


def test_catalog_max_tokens_and_fallbacks(tmp_path):
    """The catalog carries per-model max_tokens (endpoint default inheritable) and the
    ordered `fallbacks:` chain; bad chain entries are reported, never silently applied."""
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({
        "endpoints": {"e": {"kind": "openai", "base_url": "http://x/v1", "max_tokens": 9000}},
        "models": {
            "a": {"endpoint": "e", "model": "a-id", "max_tokens": 5000,
                  "fallbacks": ["a", "ghost", "b"]},
            "b": {"endpoint": "e", "model": "b-id"},
        },
    }), encoding="utf-8")
    cfg, problems = load_server_config(p)
    assert cfg.models["a"].max_tokens == 5000
    assert cfg.models["b"].max_tokens is None          # inherits at resolve time
    assert cfg.endpoints["e"].max_tokens == 9000
    assert cfg.models["a"].fallbacks == ["a", "ghost", "b"]   # kept verbatim; resolve skips
    assert any("fallbacks must not name the model itself" in x for x in problems)
    assert any("fallback 'ghost' is not a catalog model" in x for x in problems)
    assert not any("'b'" in x for x in problems)       # a valid fallback raises no problem
