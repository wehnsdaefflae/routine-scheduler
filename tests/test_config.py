"""Config loading: the deployed config.yaml keys keep loading unchanged, and both loaders
degrade per key — every invalid value becomes a problem line plus its default, never a
crash or a discarded config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rsched.config import (
    DEFAULT_BUDGETS,
    DEFAULT_PERMISSIONS,
    EndpointConfig,
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
                           "schema_mode": "json_object", "context_tokens": 180_000},
            "cc": {"kind": "anthropic"},
        },
        "models": {"ds": {"endpoint": "openrouter", "model": "deepseek/deepseek-chat",
                          "multimodal": False, "context_tokens": 200_000, "effort": "high"}},
        "system_model": "ds",
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
    assert ep.context_tokens == 180_000
    assert server.endpoints["cc"].kind == "anthropic"
    mc = server.models["ds"]
    assert mc.name == "ds" and mc.endpoint == "openrouter" and mc.model == "deepseek/deepseek-chat"
    assert mc.multimodal is False and mc.context_tokens == 200_000 and mc.effort == "high"
    assert server.system_model == "ds"
    # derived properties hang off libraries_home
    assert server.libraries_home == server.libraries_home == server.libraries_home
    assert server.rules_home == server.libraries_home / "rules"
    assert server.permissions_home == server.libraries_home / "permissions"


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


def test_server_bounds_hold_for_a_hand_edited_file(tmp_path):
    """The bounds the settings page enforces hold at load too: `max_concurrent_runs: 0` sized
    the run semaphore at zero (no run ever started, no problem reported) and a negative value
    raised out of the daemon's boot; a port outside 1–65535 cannot be bound."""
    server, problems = _load_server(tmp_path, {
        "max_concurrent_runs": 0, "registry_rescan_s": -5, "port": 70000,
        "machines": {"gpu": {"host": "h", "user": "u", "port": 0}}})
    text = " | ".join(problems)
    for key in ("max_concurrent_runs", "registry_rescan_s", "port", "machines.gpu.port"):
        assert key in text, key
    assert (server.max_concurrent_runs, server.registry_rescan_s, server.port) == (2, 30, 8321)
    assert server.machines["gpu"].port == 22


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
    never default to the Anthropic key."""
    assert EndpointConfig(name="a", kind="anthropic").key_var == "ANTHROPIC_API_KEY"
    assert EndpointConfig(name="o", kind="openai", base_url="http://x").key_var == "OPENAI_API_KEY"
    # an explicit key_var always wins over the kind default
    ep = EndpointConfig(name="o2", kind="openai", base_url="http://x", key_var="OPENROUTER_KEY")
    assert ep.key_var == "OPENROUTER_KEY"



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


def test_default_ask_timeout_is_deployment_norm(tmp_path):
    """The blocking-ask timeout default is 480min (8h) — the deployment norm — NOT 5min.
    A 5-minute default seeded a timeout trap into every new routine (recurred on
    scheduler-improvement-research + global-utils-review, each hand-fixed); pin it so a
    future edit can't silently reintroduce the trap."""
    assert DEFAULT_BUDGETS["ask_timeout_min"] == 480
    d = _mk_routine(tmp_path, {"description": "Inherits the default."})
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.budgets["ask_timeout_min"] == 480


def test_the_escalation_ladder_ships_off_and_is_the_users_to_switch_on(tmp_path):
    """`ladder.enabled` / `ladder.max_depth` are in routine.yaml rather than tuning precisely
    because authority over being supervised is not machine-tunable — and the release changes
    no live routine's behaviour, so a routine that says nothing is OFF.
    """
    d = _mk_routine(tmp_path, {"slug": "ladder-off", "description": "Inherits the default."},
                    slug="ladder-off")
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.ladder == {"enabled": False, "max_depth": 3}

    d = _mk_routine(tmp_path, {"slug": "ladder-on", "description": "Opted in.",
                               "ladder": {"enabled": True, "max_depth": 2}},
                    slug="ladder-on")
    cfg, problems = load_routine(d)
    assert problems == []
    assert cfg.ladder == {"enabled": True, "max_depth": 2}


def test_a_half_written_ladder_block_keeps_the_other_default(tmp_path):
    d = _mk_routine(tmp_path, {"slug": "ladder-half", "description": "Half.",
                               "ladder": {"enabled": True}}, slug="ladder-half")
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.ladder == {"enabled": True, "max_depth": 3}


@pytest.mark.parametrize(("slug", "block", "needle"), [
    ("lad-a", {"enabled": "yes"}, "ladder.enabled"),
    ("lad-b", {"max_depth": 0}, "ladder.max_depth"),
    ("lad-c", {"max_depth": "three"}, "ladder.max_depth"),
    ("lad-d", {"mystery": 1}, "unknown ladder key"),
])
def test_a_bad_ladder_knob_is_reported_and_leaves_the_ladder_off(tmp_path, slug, block, needle):
    """Every degradation here runs toward OFF: the only dangerous value of this field is
    `true`, so a value nobody can read must never be guessed as one."""
    d = _mk_routine(tmp_path, {"slug": slug, "description": "Bad knob.", "ladder": block},
                    slug=slug)
    cfg, problems = load_routine(d)
    assert any(needle in p for p in problems), problems
    assert cfg.ladder["enabled"] is False or "enabled" in block
    assert isinstance(cfg.ladder["max_depth"], int) and cfg.ladder["max_depth"] > 0


def test_a_bare_ladder_key_reads_as_the_defaults(tmp_path):
    d = _mk_routine(tmp_path, {"slug": "ladder-bare", "description": "Bare.", "ladder": None},
                    slug="ladder-bare")
    cfg, _ = load_routine(d)
    assert cfg.ladder == {"enabled": False, "max_depth": 3}


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


def test_a_schedule_without_a_zone_is_in_the_servers_zone(tmp_path, monkeypatch):
    """The console's schedule editor speaks the SERVER's zone and every friendly save writes it
    beside the cron, so a routine.yaml that names no zone must mean that zone too — not a fixed
    one, which fired a hand-written routine at another place's times on any other host."""
    monkeypatch.setenv("TZ", "America/New_York")
    cfg, problems = load_routine(_mk_routine(tmp_path, {"description": "x",
                                                         "schedule": {"cron": "0 7 * * *"}}))
    assert problems == [] and cfg.tz == "America/New_York"


def test_a_host_zone_zoneinfo_cannot_load_is_never_the_default(tmp_path, monkeypatch):
    """A default is never a zone the scheduler would choke on: a TZ that is not an IANA key is
    skipped like every other source server_tz cannot load, so the default is the next source's
    zone (UTC when none names one) — never the unloadable string."""
    from zoneinfo import ZoneInfo

    from rsched.schedule import server_tz

    monkeypatch.setenv("TZ", "Not/A_Zone")
    cfg, problems = load_routine(_mk_routine(tmp_path, {"description": "x"}))
    assert problems == [] and cfg.tz != "Not/A_Zone"
    assert cfg.tz == server_tz() and ZoneInfo(cfg.tz)


def test_retention_keeps_at_least_one_run(tmp_path):
    """Retention deletes `runs[keep_runs:]` after every run, so a hand-edited `keep_runs: 0`
    deleted every finished run — the one that just ended included — and a negative value
    pruned from the wrong end. The routine page refuses both; the loader now does too."""
    for bad in (0, -3):
        d = _mk_routine(tmp_path, {"description": "x", "retention": {"keep_runs": bad}},
                        slug=f"keep{abs(bad)}")
        cfg, problems = load_routine(d)
        assert cfg.keep_runs == 30
        assert any(p.startswith("retention.keep_runs:") for p in problems)


def test_a_rejected_gate_reads_like_every_other_problem_line(tmp_path):
    """`where: what`, one line per problem — not pydantic's multi-line dump with the input's
    repr and a documentation URL, which is what the routine page and `rsched validate` showed."""
    d = _mk_routine(tmp_path, {"description": "x", "run_gate": {
        "enabled": True, "timeout_s": 999, "checks": [{"kind": "nope"}]}})
    cfg, problems = load_routine(d)
    assert cfg is None
    assert "run_gate.timeout_s: Input should be less than or equal to 300" in problems
    assert any(p.startswith("run_gate.checks[0]: unknown kind 'nope'") for p in problems)
    assert not any("\n" in p or "errors.pydantic.dev" in p for p in problems)


def test_one_bad_list_item_is_dropped_and_the_rest_of_the_routine_stands(tmp_path):
    """The lenient loader could drop a bad KEY but not a bad list ITEM: the same error came back
    every round until the whole routine fell back to the defaults — its description and cron
    gone, and a deliberately locked-down routine handed the default permissions and
    capabilities — with nothing but the item's own problem line to say so."""
    d = _mk_routine(tmp_path, {"description": "a careful routine", "permissions": [],
                               "capabilities": {}, "schedule": {"cron": "0 7 * * *"},
                               "fs_read_roots": [123, "/srv/ok"]})
    cfg, problems = load_routine(d)
    assert any(p.startswith("fs_read_roots.0:") for p in problems)
    assert cfg.fs_read_roots == [Path("/srv/ok")]
    assert (cfg.description, cfg.cron, cfg.permissions) == ("a careful routine", "0 7 * * *", [])
    assert "write_util" not in cfg.capabilities.get("actions", [])
    assert not any("description is empty" in p for p in problems)


def test_a_path_naming_no_account_is_a_problem_line_not_a_crash(tmp_path):
    """`~bob/x` for an account that does not exist makes pathlib raise RuntimeError, which
    pydantic does not turn into a validation error — so both loaders, which promise a problem
    line per bad key, raised instead."""
    d = _mk_routine(tmp_path, {"description": "x",
                               "fs_read_roots": ["~nosuchuser-rsched/data", "/srv/ok"]})
    cfg, problems = load_routine(d)
    assert any(p.startswith("fs_read_roots.0:") and "nosuchuser-rsched" in p for p in problems)
    assert cfg.fs_read_roots == [Path("/srv/ok")]
    server, problems = _load_server(tmp_path, {"routines_home": "~nosuchuser-rsched/r",
                                               "port": 9000})
    assert any(p.startswith("routines_home:") for p in problems)
    assert server.port == 9000 and "nosuchuser" not in str(server.routines_home)


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


def test_unreadable_server_config_is_a_problem_line_not_a_traceback(tmp_path, monkeypatch):
    # The read itself is refused rather than the file's mode bits: root reads a 0o000 file, so a
    # chmod-based version passed as a non-root user and failed in any container running as root.
    from rsched.config import server as server_mod

    def refuse(path, default=None):
        raise PermissionError(13, "Permission denied", str(path))

    cfg = tmp_path / "config.yaml"
    cfg.write_text("token: x\n", encoding="utf-8")
    monkeypatch.setattr(server_mod, "read_yaml", refuse)
    server, problems = load_server_config(cfg)
    assert any("unreadable" in p for p in problems)
    assert server.source == cfg


def test_routine_decision_roles_load_beside_the_chat_roles(tmp_path):
    from rsched.config import load_routine
    d = tmp_path / "r"
    d.mkdir()
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "name": "R", "description": "d", "schedule": {"cron": "0 7 * * *"},
        "models": {"main": "m", "decision": "jev", "decision_media": "vl8b", "oracle": "x"}}))
    cfg, problems = load_routine(d)
    assert cfg.models == {"main": "m", "decision": "jev", "decision_media": "vl8b"}
    assert [p for p in problems if "models." in p] == [
        ("models.oracle: unknown model kind (expected one of ('main', 'tool_call', 'uncensored', "
         "'decision', 'decision_media'))")]
