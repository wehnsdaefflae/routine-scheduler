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
from rsched.engine.actions import KINDS, validate_action
from rsched.engine.executor import do_read_rule
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
    from rsched.grantpolicy import load_policy
    from rsched.grants import EMPTY_CAPABILITIES

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
    from rsched.engine.control import apply_rule_additions
    from rsched.engine.run_context import Budgets, RunContext
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


# ------------------------------------------------- the one-shot traits -> rules migration

def test_migration_promotes_orphan_routine_traits_instead_of_dropping_them(tmp_path):
    """MIGRATION(expires=2026-09-30) guard. Most per-routine trait copies are ADAPTED forks of
    a library trait and are meant to be dropped — that IS the trade. But the improver could
    author a module straight into one routine's traits/, and those exist nowhere else:
    dropping one is silent data loss, so the migration lifts it into the library first.

    A RETIRED slug is the opposite case and must NOT be promoted: its replacement ships in the
    seed, already generalized by hand, so carrying the old body over would undo exactly what
    the rename made. That is why the boot order is sync-seeds THEN migrate (cli.serve) — this
    fixture reproduces it by seeding the replacements before running.
    """
    from rsched.config import ServerConfig
    from rsched.migrate_rules import migrate_rules

    lib = tmp_path / "lib"
    (lib / "traits").mkdir(parents=True)
    (lib / "traits" / "ask-policy.md").write_text(RULE_A.replace("alpha", "ask-policy"),
                                                  encoding="utf-8")
    (lib / "traits" / "ledger-discipline.md").write_text(
        "# trait: ledger-discipline — LEDGER.md, ~40 entries, rotation\n\nold body\n",
        encoding="utf-8")
    # what sync_seed_library_docs installs at boot, before the migration runs
    (lib / "rules").mkdir()
    for slug in ("decision-record", "problem-routing", "root-cause-fix", "intent-inference"):
        (lib / "rules" / f"{slug}.md").write_text(
            f"---\ntags: [a, b, c]\n---\n# rule: {slug} — the seeded, generalized text\n\nbody\n",
            encoding="utf-8")

    routines = tmp_path / "routines"
    d = routines / "demo"
    (d / "traits").mkdir(parents=True)
    (d / "routine.yaml").write_text(
        "slug: demo\npermissions: [util-authoring, practice-library]\n"
        "capabilities: {actions: [write_util, read_trait]}\n", encoding="utf-8")
    (d / "main.md").write_text("# Run flow\n\nWork.\n", encoding="utf-8")
    for slug in ("ask-policy", "ledger-discipline", "global-utils", "maintenance-routing",
                 "correction-learning", "home-grown"):
        (d / "traits" / f"{slug}.md").write_text(
            f"# trait: {slug} — invented locally\n\nbody\n", encoding="utf-8")

    server = ServerConfig(libraries_home=lib, routines_home=routines,
                          conversations_home=tmp_path / "conv",
                          background_home=tmp_path / "bg")
    assert migrate_rules(server) == 1

    # the ORPHAN survived, as a rule, with its heading rewritten
    assert (lib / "rules" / "home-grown.md").read_text(
        encoding="utf-8").startswith("# rule: home-grown —")
    # a RETIRED slug's old body was NOT carried over under the new name
    assert "old body" not in (lib / "rules" / "decision-record.md").read_text(encoding="utf-8")
    assert not (lib / "traits").exists()

    after = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert after["rules"] == ["ask-policy", "root-cause-fix", "intent-inference",
                              "home-grown", "decision-record", "problem-routing"]
    assert "global-utils" not in after["rules"]        # became a permission
    assert "global-utils" in after["permissions"]
    assert "practice-library" not in after["permissions"]
    assert after["capabilities"]["actions"] == ["write_util"]   # read_trait is ungated now
    assert not (d / "traits").exists()
    assert "- `home-grown` —" in (d / "main.md").read_text(encoding="utf-8")
    assert migrate_rules(server) == 0                  # idempotent


def test_migration_expands_a_conflating_slug_into_every_rule_that_replaced_it():
    """`correction-learning` and `anticipatory-stewardship` each folded two independent
    principles into one local module: what the user WANTED, and why they had to say it. The
    map expands to BOTH so a routine keeps both halves instead of silently losing one.
    """
    from rsched.migrate_rules import _SLUG_MAP

    for slug in ("correction-learning", "anticipatory-stewardship"):
        assert _SLUG_MAP[slug] == ("root-cause-fix", "intent-inference")
    # maintenance-routing split the other way: the reporting half generalized, the instance
    # ownership table went to the one routine that needs it (not a rule at all)
    assert _SLUG_MAP["maintenance-routing"] == ("problem-routing",)
    assert _SLUG_MAP["global-utils"] == ()             # became a permission


def test_migration_drops_the_retired_permission_doc_from_a_live_library(tmp_path):
    """Deleting a doc from library-seed/ does NOT reach an existing instance: the seed sync
    only ADDS (it must never clobber the user's own docs). `practice-library` requires
    `read_trait`, which is no longer an action kind, so left in place it fails the library
    lint on every page load — which is exactly how it was found, after the first deploy.

    Runs even once the main conversion has converged: traits/ is already gone by then.
    """
    from rsched.config import ServerConfig
    from rsched.migrate_rules import migrate_rules
    from rsched.workflows.lint import lint_permission_text

    lib = tmp_path / "lib"
    (lib / "permissions").mkdir(parents=True)
    (lib / "rules").mkdir()
    doomed = lib / "permissions" / "practice-library.md"
    doomed.write_text("---\ntags: [a, b, c]\nrequires:\n  actions: [read_trait]\n---\n"
                      "# permission: practice library — retired\n\nbody\n", encoding="utf-8")
    keeper = lib / "permissions" / "memory.md"
    keeper.write_text("---\ntags: [a, b, c]\nrequires:\n  actions: [memory_read]\n---\n"
                      "# permission: memory — kept\n\nbody\n", encoding="utf-8")
    # the state that made this visible: the doc cannot lint clean any more
    assert any("read_trait" in p for p in lint_permission_text(
        doomed.read_text(encoding="utf-8"), filename="practice-library.md"))

    server = ServerConfig(libraries_home=lib, routines_home=tmp_path / "routines",
                          conversations_home=tmp_path / "conv", background_home=tmp_path / "bg")
    assert not (lib / "traits").exists()      # main conversion already converged
    migrate_rules(server)
    assert not doomed.exists()
    assert keeper.is_file()                   # only the named docs go
    migrate_rules(server)                     # idempotent


def test_migration_rewrites_library_workflow_includes(tmp_path):
    """The same trap as the retired permission doc, one layer up and wider. Editing a pattern
    in library-seed/ does not reach a live instance, and the library also carries patterns the
    seed never had (curator-drafted). Left alone each lints red AND seeds new routines with
    rule slugs that no longer exist. Found on the running instance after the 0.165.0 deploy.
    """
    from rsched.config import ServerConfig
    from rsched.migrate_rules import migrate_rules
    from rsched.workflows.lint import lint_workflow_py

    lib = tmp_path / "lib"
    (lib / "workflows").mkdir(parents=True)
    (lib / "rules").mkdir()
    for slug in ("ask-policy", "web-research", "decision-record", "problem-routing",
                 "root-cause-fix", "intent-inference"):
        (lib / "rules" / f"{slug}.md").write_text(
            f"---\ntags: [a, b, c]\n---\n# rule: {slug} — x\n\nbody\n", encoding="utf-8")

    # a multi-line literal, so the AST edit has to survive formatting it cannot predict
    wf = lib / "workflows" / "curator-drafted.py"
    wf.write_text(
        'META = {\n    "slug": "curator-drafted",\n'
        '    "includes": ["ask-policy", "global-utils", "ledger-discipline",\n'
        '                 "maintenance-routing"],\n}\n\n\ndef main():\n    pass\n',
        encoding="utf-8")

    server = ServerConfig(libraries_home=lib, routines_home=tmp_path / "routines",
                          conversations_home=tmp_path / "conv", background_home=tmp_path / "bg")
    migrate_rules(server)

    after = wf.read_text(encoding="utf-8")
    assert '"includes": ["ask-policy", "decision-record", "problem-routing"]' in after
    assert "global-utils" not in after        # became a permission — not a rule any more
    assert "ledger-discipline" not in after
    assert "def main():" in after            # only the literal was touched
    # the defect this fixes: every include now resolves to a real rule (the fixture's META
    # is minimal, so other lint complaints are expected and not what is under test)
    known = [p.stem for p in (lib / "rules").glob("*.md")]
    problems = lint_workflow_py(after, filename=wf.name, rule_slugs=known)
    assert not [p for p in problems if "does not resolve" in p], problems
    migrate_rules(server)                    # idempotent
    assert wf.read_text(encoding="utf-8") == after


def test_migration_dereferences_trait_paths_in_stage_prose(tmp_path):
    """MIGRATION(expires=2026-09-30) guard for the R297 completion pass. An ALREADY-converted
    routine (traits/ long gone) whose stage prose still says `traits/<slug>.md` gets every
    reference rewritten through the same slug map the rest of the migration used — enclosing
    backticks consumed so no code span nests, unknown slugs left in place (loudly) — and the
    pass is idempotent.
    """
    from rsched.config import ServerConfig
    from rsched.migrate_rules import migrate_rules

    lib = tmp_path / "lib"
    (lib / "rules").mkdir(parents=True)
    for slug in ("decision-record", "problem-routing", "root-cause-fix", "intent-inference",
                 "web-research"):
        (lib / "rules" / f"{slug}.md").write_text(
            f"---\ntags: [a, b, c]\n---\n# rule: {slug} — seeded\n\nbody\n", encoding="utf-8")

    routines = tmp_path / "routines"
    d = routines / "demo"
    (d / "stages").mkdir(parents=True)
    (d / "routine.yaml").write_text("slug: demo\nrules: [web-research]\n", encoding="utf-8")
    (d / "main.md").write_text(
        "# Demo\n\nVerify facts (see `traits/web-research.md`).\n", encoding="utf-8")
    (d / "stages" / "record.md").write_text(
        "Append ONE entry (consult traits/ledger-discipline.md). Route problems via\n"
        "traits/maintenance-routing.md; tools per traits/global-utils.md.\n"
        "After corrections read traits/correction-learning.md.\n"
        "Custom: traits/home-grown-notion.md stays.\n", encoding="utf-8")

    server = ServerConfig(libraries_home=lib, routines_home=routines,
                          conversations_home=tmp_path / "conv",
                          background_home=tmp_path / "bg")
    assert migrate_rules(server) == 0          # nothing traits/-dir-shaped left to convert

    main = (d / "main.md").read_text(encoding="utf-8")
    assert "traits/" not in main
    assert "(see the `web-research` rule)" in main       # backticks consumed, nothing nests
    stage = (d / "stages" / "record.md").read_text(encoding="utf-8")
    assert "(consult the `decision-record` rule)" in stage
    assert "via\nthe `problem-routing` rule" in stage
    assert "tools per your global-utils permission notes" in stage
    assert "read the `root-cause-fix` + `intent-inference` rules" in stage
    assert "Custom: traits/home-grown-notion.md stays" in stage   # unknown slug: untouched
    before = stage
    assert migrate_rules(server) == 0                    # idempotent…
    assert (d / "stages" / "record.md").read_text(encoding="utf-8") == before   # …and stable
