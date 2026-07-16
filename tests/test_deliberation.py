"""The deliberation level — the user's knob over how much thinking lands on paper:
tuning.yaml loading (config = authority stays sealed; tuning = machine-tunable behavior,
recipe-classed), the per-level say contracts, and the mid-run control.json switch.
"""

from types import SimpleNamespace

import yaml

from rsched.config import (
    DEFAULT_DELIBERATION,
    DELIBERATION_LEVELS,
    ServerConfig,
    load_routine,
    load_tuning,
    write_tuning,
)
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


# ---- tuning.yaml: the machine-tunable behavior file -------------------------------------


def test_config_reads_the_level_from_tuning_yaml(make_routine):
    d = make_routine(slug="delib-cfg")
    cfg, problems = load_routine(d)
    assert cfg.deliberation == DEFAULT_DELIBERATION   # no tuning.yaml → default
    assert not problems

    write_tuning(d, {"deliberation": "deliberate"})
    cfg, problems = load_routine(d)
    assert cfg.deliberation == "deliberate"
    assert not problems

    (d / "tuning.yaml").write_text("deliberation: verbose\nmystery: 1\n", encoding="utf-8")
    cfg, problems = load_routine(d)
    assert cfg.deliberation == DEFAULT_DELIBERATION   # bad value → reported, not applied
    assert any("unknown level" in p for p in problems)
    assert any("mystery" in p for p in problems)      # unknown tuning keys are reported


def test_routine_yaml_never_carries_deliberation(make_routine):
    """Canonical form: the key lives in tuning.yaml ONLY — a routine.yaml key is stale
    data, reported and ignored (never read)."""
    d = make_routine(slug="delib-stale")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["deliberation"] = "think-on-paper"
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg, problems = load_routine(d)
    assert cfg.deliberation == DEFAULT_DELIBERATION
    assert any("belongs in tuning.yaml" in p for p in problems)


def test_load_tuning_survives_broken_yaml(make_routine):
    d = make_routine(slug="delib-broken")
    (d / "tuning.yaml").write_text(":: not yaml ::", encoding="utf-8")
    values, problems = load_tuning(d)
    assert values == {}
    assert problems and "tuning.yaml" in problems[0]


# ---- the write boundary: tuning is recipe, config stays sealed --------------------------


def test_tuning_yaml_is_recipe_classed(make_routine, tmp_path):
    # own tuning.yaml: sealed for an ordinary run, open under recipe_unlocked
    locked = GrantPolicy()
    denial = locked.deny({"kind": "write_file", "path": "tuning.yaml", "content": "x"})
    assert denial and "recipe" in denial
    assert GrantPolicy(recipe_unlocked=True).deny(
        {"kind": "write_file", "path": "tuning.yaml", "content": "x"}) is None

    ctx = _ctx(make_routine, tmp_path, slug="plain-run")
    ctx.grants = GrantPolicy()
    obs = do_write_file({"path": str(ctx.routine.dir / "tuning.yaml"),
                         "content": "deliberation: terse\n"}, ctx)
    assert "recipe" in obs["error"]


def test_improver_tunes_a_target_through_tuning_yaml(make_routine, tmp_path):
    """The improver's whole flow, no key-level machinery: fs_write_roots + recipe_unlocked
    let it edit a target's tuning.yaml like any recipe file; the loader applies it."""
    target = make_routine(slug="delib-target")
    write_tuning(target, {"deliberation": "standard"})
    ctx = _ctx(make_routine, tmp_path, slug="improver-like")
    ctx.routine.fs_write_roots = [target.parent]
    ctx.grants = GrantPolicy(recipe_unlocked=True)
    obs = do_edit_file({"path": str(target / "tuning.yaml"),
                        "anchor": "deliberation: standard",
                        "replacement": "deliberation: deliberate"}, ctx)
    assert "error" not in obs, obs
    cfg, problems = load_routine(target)
    assert cfg.deliberation == "deliberate"
    assert not problems


def test_routine_yaml_stays_sealed_for_everyone(make_routine, tmp_path):
    """The invariant is absolute again: config is the user's — no run edits routine.yaml,
    not even the improver, and the denials route to tuning.yaml for the knobs."""
    target = make_routine(slug="delib-sealed")
    ctx = _ctx(make_routine, tmp_path, slug="improver-like2")
    ctx.routine.fs_write_roots = [target.parent]
    ctx.grants = GrantPolicy(recipe_unlocked=True)
    obs = do_edit_file({"path": str(target / "routine.yaml"),
                        "anchor": "enabled: true", "replacement": "enabled: false"}, ctx)
    assert "tuning.yaml" in obs["error"]              # the error teaches the right channel
    denial = GrantPolicy(recipe_unlocked=True).deny(
        {"kind": "write_file", "path": "routine.yaml", "content": "x"})
    assert denial and "tuning.yaml" in denial


# ---- the wording module ------------------------------------------------------------------


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


# ---- the mid-run switch (control.json → turn boundary) ------------------------------------


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


# ---- wizard suggestion fallback ------------------------------------------------------------


def test_suggest_falls_back_to_default_level(tmp_path):
    from rsched.workflows.suggest import suggest_traits_permissions

    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"    # empty library → no LLM call
    out = suggest_traits_permissions(server, "Watch arxiv and rank new agent papers.")
    assert out["deliberation"] == DEFAULT_DELIBERATION
