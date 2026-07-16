"""The deliberation level — the user's knob over how much thinking lands on paper:
config validation, the per-level say contracts, the mid-run control.json switch, and the
ONE routine.yaml carve-out (a config_tunable run may re-level `deliberation`, nothing else).
"""

from types import SimpleNamespace

import yaml

from rsched.config import DEFAULT_DELIBERATION, DELIBERATION_LEVELS, ServerConfig, load_routine
from rsched.engine import deliberation
from rsched.engine.composer import harness_contract
from rsched.engine.control import apply_deliberation_switch
from rsched.engine.executor import do_edit_file, do_write_file
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript, read_events
from rsched.grants import GrantPolicy
from rsched.paths import atomic_write_json


def _ctx(make_routine, tmp_path, **kwargs) -> RunContext:
    d = make_routine(**kwargs)
    cfg, _problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260716-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260716-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.deliberation = cfg.deliberation
    return ctx


# ---- config ---------------------------------------------------------------------------


def test_config_default_and_invalid_level(make_routine):
    d = make_routine(slug="delib-cfg")
    cfg, problems = load_routine(d)
    assert cfg.deliberation == DEFAULT_DELIBERATION   # silent yaml → default
    assert not [p for p in problems if "deliberation" in p]

    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["deliberation"] = "verbose"                   # not a level
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg, problems = load_routine(d)
    assert cfg.deliberation == DEFAULT_DELIBERATION   # coerced, like unknown budgets
    assert any("deliberation" in p for p in problems)


# ---- the wording module ---------------------------------------------------------------


def test_levels_have_distinct_contracts_and_only_top_adds_the_standing_note():
    contracts = {deliberation.say_contract(lv) for lv in DELIBERATION_LEVELS}
    assert len(contracts) == 3          # think-on-paper shares the deliberate sentence
    assert deliberation.standing_note("think-on-paper").startswith("Deliberation is part")
    assert all(deliberation.standing_note(lv) == ""
               for lv in ("terse", "standard", "deliberate"))
    # the upper stops license knowledge beyond the run — the point of the knob
    assert "beyond this run" in deliberation.say_contract("deliberate")
    note = deliberation.switch_note("standard", "terse")
    assert "standard → terse" in note
    assert deliberation.say_contract("terse") in note
    # a switch INTO think-on-paper must carry the notes-file discipline too
    assert deliberation.standing_note("think-on-paper") in deliberation.switch_note(
        "standard", "think-on-paper")


def test_composer_words_the_contract_by_level(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, slug="delib-composer")
    for level, marker in [("terse", "ONE terse clause"),
                          ("standard", "lead with what the last observation taught you"),
                          ("deliberate", "beyond this run"),
                          ("think-on-paper", "state/notes.md")]:
        ctx.deliberation = level
        assert marker in harness_contract(ctx), level


# ---- the mid-run switch (control.json → turn boundary) ---------------------------------


def test_control_switch_applies_once_and_notes_the_new_contract(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path, slug="delib-switch")
    loop = SimpleNamespace(ctx=ctx, messages=[], _last_deliberation_ts="")
    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"set_deliberation": {"level": "deliberate", "ts": "t1"}})
    apply_deliberation_switch(loop)
    assert ctx.deliberation == "deliberate"
    assert len(loop.messages) == 1
    assert deliberation.say_contract("deliberate") in loop.messages[0]["content"]
    events, _off = read_events(ctx.run_dir / "transcript.jsonl")
    assert any(e["type"] == "user_injection" and e["payload"].get("source") == "engine"
               for e in events)
    apply_deliberation_switch(loop)                   # same ts → edge-triggered no-op
    assert len(loop.messages) == 1

    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"set_deliberation": {"level": "bogus", "ts": "t2"}})
    apply_deliberation_switch(loop)                   # unknown level → ignored, no note
    assert ctx.deliberation == "deliberate"
    assert len(loop.messages) == 1


# ---- the routine.yaml carve-out ---------------------------------------------------------


def _improver_ctx(make_routine, tmp_path, target_dir) -> RunContext:
    """A run shaped like the improver: fs_write_roots cover the target, config_tunable on."""
    ctx = _ctx(make_routine, tmp_path, slug="improver-like")
    ctx.routine.fs_write_roots = [target_dir.parent]
    ctx.grants = GrantPolicy(config_tunable=True, recipe_unlocked=False)
    return ctx


def test_carveout_allows_a_deliberation_only_edit(make_routine, tmp_path):
    target = make_routine(slug="delib-target")
    ctx = _improver_ctx(make_routine, tmp_path, target)
    obs = do_edit_file({"path": str(target / "routine.yaml"),
                        "anchor": "description: A test routine.",
                        "replacement": "description: A test routine.\ndeliberation: deliberate"},
                       ctx)
    assert "error" not in obs, obs
    cfg, problems = load_routine(target)
    assert cfg.deliberation == "deliberate"
    assert not problems


def test_carveout_rejects_any_other_key_and_bad_levels(make_routine, tmp_path):
    target = make_routine(slug="delib-target2")
    ctx = _improver_ctx(make_routine, tmp_path, target)
    before = (target / "routine.yaml").read_text(encoding="utf-8")

    obs = do_edit_file({"path": str(target / "routine.yaml"),
                        "anchor": "enabled: true", "replacement": "enabled: false"}, ctx)
    assert "deliberation" in obs["error"]             # names the one tunable key

    obs = do_edit_file({"path": str(target / "routine.yaml"),
                        "anchor": "description: A test routine.",
                        "replacement": "description: A test routine.\ndeliberation: max"},
                       ctx)
    assert "must be one of" in obs["error"]

    # write_file (whole-document) passes only when the parsed diff is deliberation-only
    ctx.seen_paths.add(str(target / "routine.yaml"))  # grounding: the improver read it first
    raw = yaml.safe_load(before)
    raw["deliberation"] = "terse"
    raw["budgets"]["max_turns"] = 99                  # smuggled config change
    obs = do_write_file({"path": str(target / "routine.yaml"),
                         "content": yaml.safe_dump(raw)}, ctx)
    assert "budgets" in obs["error"]
    assert (target / "routine.yaml").read_text(encoding="utf-8") == before  # untouched

    del raw["budgets"]["max_turns"]
    raw["budgets"] = yaml.safe_load(before)["budgets"]
    obs = do_write_file({"path": str(target / "routine.yaml"),
                         "content": yaml.safe_dump(raw)}, ctx)
    assert "error" not in obs, obs
    assert load_routine(target)[0].deliberation == "terse"


def test_without_the_grant_routine_yaml_stays_sealed(make_routine, tmp_path):
    target = make_routine(slug="delib-sealed")
    ctx = _ctx(make_routine, tmp_path, slug="plain-run")
    ctx.routine.fs_write_roots = [target.parent]      # roots alone are not enough…
    ctx.grants = GrantPolicy(config_tunable=False)    # …the policy flag gates the carve-out
    obs = do_edit_file({"path": str(target / "routine.yaml"),
                        "anchor": "description: A test routine.",
                        "replacement": "description: A test routine.\ndeliberation: terse"},
                       ctx)
    assert "fs_write_root" in obs["error"]

    policy = GrantPolicy(config_tunable=False)
    denial = policy.deny({"kind": "edit_file", "path": "routine.yaml",
                          "anchor": "x", "replacement": "y"})
    assert denial and "deliberation" in denial        # the denial teaches the exception
    assert GrantPolicy(config_tunable=True).deny(
        {"kind": "edit_file", "path": "routine.yaml", "anchor": "x", "replacement": "y"}) is None


# ---- wizard suggestion fallback ---------------------------------------------------------


def test_suggest_falls_back_to_default_level(tmp_path):
    from rsched.workflows.suggest import suggest_traits_permissions

    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"    # empty library → no LLM call
    out = suggest_traits_permissions(server, "Watch arxiv and rank new agent papers.")
    assert out["deliberation"] == DEFAULT_DELIBERATION
