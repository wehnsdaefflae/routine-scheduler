"""The general-rules layer: routine.yaml's `rules:` as the state, the derived Standing-practices
tail, the library-global `read_rule`, authoring via `write_rule`, and the mid-run control.json
hand-off.

The invariants under test: a rule has exactly ONE copy (the library — nothing is ever written
into a routine dir), the held SET is config only the user changes, and the PROSE is changeable
only by a routine holding the rule-authoring capability, under its own approval dial.
"""

from types import SimpleNamespace

import pytest
import yaml

from rsched import rules as rules_mod
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import KINDS
from rsched.engine.memops import do_read_rule
from rsched.engine.observations import format_observation
from rsched.grants import GATED_KINDS
from rsched.rules import PRACTICES_HEADING

RULE_A = """---
tags: [a, b, c]
---
# rule: alpha — the first principle

- **Do the thing.** Carefully.
"""

RULE_B = """---
tags: [a, b, c]
---
# rule: beta — the second principle

- **Do the other thing.** Also carefully.
"""


@pytest.fixture
def lib(tmp_path):
    home = tmp_path / "library" / "rules"
    home.mkdir(parents=True)
    (home / "alpha.md").write_text(RULE_A, encoding="utf-8")
    (home / "beta.md").write_text(RULE_B, encoding="utf-8")
    return home


@pytest.fixture
def routine(tmp_path):
    d = tmp_path / "routines" / "demo"
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text("slug: demo\nrules: []\n", encoding="utf-8")
    (d / "main.md").write_text("# Run flow\n\nDo the work.\n", encoding="utf-8")
    return d


def _held(routine):
    return yaml.safe_load((routine / "routine.yaml").read_text(encoding="utf-8"))["rules"]


def test_binding_records_a_slug_and_copies_nothing(lib, routine):
    rules_mod.apply_changes(lib, routine, ["alpha"], [])
    assert _held(routine) == ["alpha"]
    # the whole point of the layer: no per-routine copy exists to drift from the library
    assert not (routine / "rules").exists()
    assert not list(routine.glob("**/alpha.md"))
    main = (routine / "main.md").read_text(encoding="utf-8")
    assert PRACTICES_HEADING in main
    assert "- `alpha` — the first principle" in main


def test_tail_is_derived_so_unbinding_prunes_it(lib, routine):
    rules_mod.apply_changes(lib, routine, ["alpha", "beta"], [])
    assert rules_mod.current_rules(routine) == ["alpha", "beta"]
    rules_mod.apply_changes(lib, routine, [], ["alpha"])
    main = (routine / "main.md").read_text(encoding="utf-8")
    assert "`alpha`" not in main
    assert "`beta`" in main
    assert "Do the work." in main          # the body above the tail is never touched
    # last one out removes the section entirely rather than leaving an empty heading
    rules_mod.apply_changes(lib, routine, [], ["beta"])
    assert PRACTICES_HEADING not in (routine / "main.md").read_text(encoding="utf-8")


def test_tail_rebuild_is_idempotent_and_converges(lib, routine):
    rules_mod.apply_changes(lib, routine, ["alpha"], [])
    first = (routine / "main.md").read_text(encoding="utf-8")
    rules_mod.sync_practices_tail(routine, lib)
    rules_mod.sync_practices_tail(routine, lib)
    assert (routine / "main.md").read_text(encoding="utf-8") == first


def test_a_library_edit_reaches_every_holder_with_no_migration(lib, routine):
    """The property the per-routine copies gave up: revise once, every holder reads the new
    text. Nothing in the routine dir records the prose, so there is nothing to re-sync.
    """
    rules_mod.apply_changes(lib, routine, ["alpha"], [])
    (lib / "alpha.md").write_text(RULE_A.replace("Carefully.", "Very carefully."),
                                  encoding="utf-8")
    ctx = _ctx(lib, routine)
    obs = do_read_rule({"kind": "read_rule", "name": "alpha"}, ctx)
    assert "Very carefully." in obs["content"]


def test_apply_changes_reports_only_real_mutations(lib, routine):
    added, removed = rules_mod.apply_changes(lib, routine, ["alpha"], [])
    assert (added, removed) == (["alpha"], [])
    # re-binding a held rule and unbinding an absent one are both no-ops, not errors
    added, removed = rules_mod.apply_changes(lib, routine, ["alpha"], ["beta"])
    assert (added, removed) == ([], [])


def test_unknown_slug_raises(lib, routine):
    with pytest.raises(KeyError):
        rules_mod.apply_changes(lib, routine, ["nope"], [])
    with pytest.raises(KeyError):
        rules_mod.apply_changes(lib, routine, ["../escape"], [])


def _ctx(lib, routine):
    return SimpleNamespace(server=SimpleNamespace(rules_home=lib),
                           routine=SimpleNamespace(dir=routine,
                                                   rules=rules_mod.current_rules(routine)))


def test_read_rule_returns_prose_without_writing_anything(lib, routine):
    obs = do_read_rule({"kind": "read_rule", "name": "alpha"}, _ctx(lib, routine))
    assert "the first principle" in obs["content"]
    assert obs["held"] is False
    # reading a rule you do not hold must not bind it — that is the user's call alone
    assert rules_mod.current_rules(routine) == []
    assert PRACTICES_HEADING not in (routine / "main.md").read_text(encoding="utf-8")
    assert "applies for the rest of this run only" in format_observation(obs)


def test_read_rule_flags_rules_that_bind(lib, routine):
    rules_mod.apply_changes(lib, routine, ["alpha"], [])
    obs = do_read_rule({"kind": "read_rule", "name": "alpha"}, _ctx(lib, routine))
    assert obs["held"] is True
    assert "this rule BINDS you" in format_observation(obs)


def test_read_rule_list_and_missing(lib, routine):
    rules_mod.apply_changes(lib, routine, ["beta"], [])
    obs = do_read_rule({"kind": "read_rule", "name": "list"}, _ctx(lib, routine))
    assert {r["slug"]: r["held"] for r in obs["rules"]} == {"alpha": False, "beta": True}
    assert "binds you" in format_observation(obs)
    missing = do_read_rule({"kind": "read_rule", "name": "ghost"}, _ctx(lib, routine))
    assert missing["missing"] is True
    assert "alpha" in format_observation(missing)


def test_read_rule_is_ungated_so_a_routine_can_read_what_binds_it():
    """Deliberately NOT a capability: a routine unable to read its own standing practices
    would hold rules it cannot follow, and library prose has no side effect to gate.
    """
    assert "read_rule" in KINDS
    assert "read_rule" not in GATED_KINDS
    assert validate_action({"say": "s", "kind": "read_rule", "name": "alpha"}) == []


def test_write_rule_is_gated_and_carries_its_own_approval_dial(tmp_path):
    from rsched.grants import EMPTY_CAPABILITIES
    from rsched.policyload import load_policy

    assert "write_rule" in GATED_KINDS
    policy = load_policy(tmp_path, [], {"actions": ["write_rule"], "rule_confirm": "creations"})
    assert policy.allows_kind("write_rule")
    assert policy.needs_rule_confirm(creating=True) is True
    assert policy.needs_rule_confirm(creating=False) is False
    # the util dial is untouched by it: authoring your own tools is a different decision
    assert policy.needs_confirm(creating=False) is True
    assert "rule_confirm" in EMPTY_CAPABILITIES


def test_user_bound_rule_reaches_a_live_run_once(make_routine, tmp_path, lib):
    """Config alone cannot reach a run in flight — its prompt was composed at boot and is
    immutable — so the web layer signals control.json and the engine appends the prose from
    the LIBRARY at the next turn boundary, exactly once per signal.
    """
    from rsched.config import ServerConfig, load_routine
    from rsched.engine.budgets_config import Budgets
    from rsched.engine.run_context import RunContext
    from rsched.engine.switches import apply_rule_additions
    from rsched.engine.transcript import Transcript, read_events
    from rsched.paths import atomic_write_json

    d = make_routine(slug="rule-live")
    cfg, _ = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260721-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig(libraries_home=lib.parent)
    ctx = RunContext(routine=cfg, server=server, registry=None,
                     run_ts="20260721-070000", run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    loop = SimpleNamespace(ctx=ctx, messages=[], _last_rules_ts="")
    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"add_rules": {"slugs": ["alpha"], "ts": "t1"}})
    apply_rule_additions(loop)
    assert len(loop.messages) == 1
    assert "the first principle" in loop.messages[0]["content"]
    assert "applies from now on" in loop.messages[0]["content"]
    events, _off = read_events(ctx.run_dir / "transcript.jsonl")
    assert any(e["type"] == "user_injection" and e["payload"].get("source") == "engine"
               for e in events)
    apply_rule_additions(loop)                      # same ts → edge-triggered no-op
    assert len(loop.messages) == 1
    # a fresh signal repeating a slug already delivered must not re-append the prose
    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"add_rules": {"slugs": ["alpha"], "ts": "t2"}})
    apply_rule_additions(loop)
    assert len(loop.messages) == 1
