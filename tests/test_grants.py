"""The two-layer permission set: capabilities normalization, the requires: library index,
activation/deactivation cascades, policy derivation from the routine's OWN capabilities,
and the per-kind denial messages validate_action surfaces."""

from __future__ import annotations

from pathlib import Path

from rsched.grants import (
    EMPTY_CAPABILITIES,
    GrantPolicy,
    capabilities_for,
    floor_capabilities,
    load_policy,
    normalize_capabilities,
    read_library_requires,
    unsatisfied_requires,
)


def _lib(tmp_path: Path, permissions: dict[str, str]) -> Path:
    home = tmp_path / "library" / "permissions"
    home.mkdir(parents=True, exist_ok=True)
    for slug, text in permissions.items():
        (home / f"{slug}.md").write_text(text, encoding="utf-8")
    return home


AUTHORING = """---
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util]
---
# permission: util authoring — create and revise utils
body
"""

COMMUNICATION = """---
tags: [communication, policy, notification]
requires:
  utils: [discord]
---
# permission: communication — Discord as a second decision surface
body
"""

RUN_HISTORY = """---
tags: [history, record-keeping, self-management]
requires:
  runs: last
---
# permission: run history — read previous runs
body
"""

WORKFLOW_GEN = """---
tags: [decomposition, workflows, self-management]
requires:
  workflows: generate
---
# permission: workflow generation — draft a new pattern when none fits
body
"""


# ------------------------------------------------------------- normalize_capabilities


def test_normalize_capabilities_accepts_the_schema():
    c, problems = normalize_capabilities({"actions": ["util", "write_util"],
                                          "utils": ["discord"], "confirm": "always"})
    assert problems == []
    assert c == {"actions": ["util", "write_util"], "utils": ["discord"], "confirm": "always"}
    # only the canonical vocabulary is accepted — legacy true/false/revisions-only is gone
    for legacy in (True, False, "revisions-only"):
        got, probs = normalize_capabilities({"confirm": legacy})
        assert got == {} and any("confirm" in p for p in probs)
    assert normalize_capabilities({"confirm": "creations"})[0] == {"confirm": "creations"}
    assert normalize_capabilities({"runs": "none"})[0] == {"runs": "none"}
    assert normalize_capabilities({"runs": "all"})[0] == {"runs": "all"}
    assert normalize_capabilities(None) == ({}, [])


def test_normalize_capabilities_reports_and_drops_invalid_parts():
    c, problems = normalize_capabilities({"actions": ["util", "dance"], "utils": ["Not A Slug"],
                                          "confirm": "sometimes", "shell": True,
                                          "runs": "some", "self_modify": True})
    text = " | ".join(problems)
    assert "'dance' is not an action kind" in text
    assert "'Not A Slug' is not a kebab-case util name" in text
    assert "confirm must be always, creations or never" in text
    assert "capabilities.shell: unknown key" in text
    assert "runs must be none or last or all" in text
    assert "capabilities.self_modify: unknown key" in text
    assert c == {"actions": ["util"], "utils": []}      # invalid entries dropped, valid kept
    assert normalize_capabilities("write_util")[1]      # non-mapping → problem
    bad_list, problems2 = normalize_capabilities({"actions": "util"})
    assert bad_list == {} and any("must be a list" in p for p in problems2)


def test_requires_mode_rejects_confirm_and_runs_none():
    """A doc may not demand an approval level (user policy) nor 'runs: none' (that is
    the absence of a requirement)."""
    req, problems = normalize_capabilities({"actions": ["write_util"], "confirm": True},
                                           label="requires", requires=True)
    assert req == {"actions": ["write_util"]}
    assert any("requires.confirm: unknown key" in p for p in problems)
    _, p2 = normalize_capabilities({"runs": "none"}, label="requires", requires=True)
    assert any("runs must be last or all" in p for p in p2)


# ------------------------------------------------------------------ library requires


def test_requires_read_from_library_only(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "plain": "# permission: plain — no requires\nbody\n"})
    lib = read_library_requires(home)
    assert set(lib) == {"util-authoring", "communication"}   # requires-less docs omitted
    assert lib["util-authoring"] == {"actions": ["write_util"]}
    assert read_library_requires(tmp_path / "nowhere") == {}   # missing library → none


def test_broken_frontmatter_degrades_to_no_requires(tmp_path):
    home = _lib(tmp_path, {"broken": "---\nrequires: [not: closed\n---\n# permission: broken — x\n"})
    assert read_library_requires(home) == {}


# ------------------------------------------------------------------------- cascades


def test_capabilities_for_raises_the_base_to_cover_active_docs(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "run-history": RUN_HISTORY})
    lib = read_library_requires(home)
    caps = capabilities_for(["util-authoring", "communication", "run-history"], lib)
    assert caps == {"actions": ["write_util"], "utils": ["discord"],
                    "confirm": "always", "runs": "last", "workflows": "catalog"}
    # base values survive and only rise: runs stays at the deeper level, confirm untouched
    base = {"actions": ["memory_read"], "utils": [], "confirm": "never", "runs": "all"}
    caps2 = capabilities_for(["run-history"], lib, base)
    assert caps2["runs"] == "all" and caps2["confirm"] == "never"
    assert caps2["actions"] == ["memory_read"]
    assert capabilities_for([], lib) == EMPTY_CAPABILITIES


def test_floor_capabilities_binds_gated_capabilities_to_held_permissions(tmp_path):
    """D8: a gated action / reserved util / run access survives only as the MEANS of a HELD
    permission; the confirm level and run depth remain user policy under it. raise+floor
    together == exactly the union of the held docs' requires (plus those policy dials)."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "run-history": RUN_HISTORY})
    lib = read_library_requires(home)
    orphan = {"actions": ["write_util"], "utils": ["discord"], "confirm": "never", "runs": "all"}
    # nothing held → every gated capability is floored away (confirm dial preserved)
    assert floor_capabilities([], lib, orphan) == {
        "actions": [], "utils": [], "confirm": "never", "runs": "none", "workflows": "catalog"}
    # util-authoring held → write_util survives (with its policy); discord + runs still floored
    assert floor_capabilities(["util-authoring"], lib, orphan) == {
        "actions": ["write_util"], "utils": [], "confirm": "never", "runs": "none",
        "workflows": "catalog"}
    # run-history held → run DEPTH (a user dial) is kept above none; actions/utils floored
    kept = floor_capabilities(["run-history"], lib, orphan)
    assert kept["runs"] == "all" and kept["actions"] == [] and kept["utils"] == []
    # raise THEN floor == exactly the held docs' requires + policy dials, no contradiction
    active = ["util-authoring", "communication", "run-history"]
    assert floor_capabilities(active, lib, capabilities_for(active, lib)) == {
        "actions": ["write_util"], "utils": ["discord"], "confirm": "always", "runs": "last",
        "workflows": "catalog"}


def test_floor_keeps_gated_kind_via_default_source_when_doc_predates_it(tmp_path):
    """Regression (remove_util toggle reverted on save): a gated kind whose permission doc's
    requires: predates the kind — util-authoring.md was seeded before remove_util existed —
    must still persist when the user EXPLICITLY opts in AND holds the canonical source
    permission (_DEFAULT_KIND_SOURCE). Otherwise floor_capabilities strips it every save."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING})   # requires: [write_util] only
    lib = read_library_requires(home)
    opt_in = {"actions": ["write_util", "remove_util"]}
    # util-authoring held + explicit opt-in → remove_util survives the floor (order kept)
    assert floor_capabilities(["util-authoring"], lib, opt_in)["actions"] == \
        ["write_util", "remove_util"]
    # not held → floored away entirely
    assert floor_capabilities([], lib, opt_in)["actions"] == []
    # RAISE is unchanged: merely holding util-authoring does NOT auto-add remove_util
    assert capabilities_for(["util-authoring"], lib)["actions"] == ["write_util"]


def test_workflows_generate_capability_binds_to_its_permission(tmp_path):
    """`workflows: generate` (draft a pattern for a subtask when none fits) rides the same
    cascade as `runs`: off by default, raised by its doc, floored away without it, and
    surfaced as GrantPolicy.may_generate_workflow()."""
    home = _lib(tmp_path, {"workflow-generation": WORKFLOW_GEN})
    lib = read_library_requires(home)
    assert lib["workflow-generation"] == {"workflows": "generate"}
    # raise: holding the doc lifts workflows to generate
    assert capabilities_for(["workflow-generation"], lib)["workflows"] == "generate"
    # floor: an orphan generate capability with no held doc falls back to catalog
    orphan = {"workflows": "generate"}
    assert floor_capabilities([], lib, orphan)["workflows"] == "catalog"
    assert floor_capabilities(["workflow-generation"], lib, orphan)["workflows"] == "generate"
    # unsatisfied: the doc's requirement is named when the mapping doesn't cover it
    assert unsatisfied_requires(["workflow-generation"], {}, lib) == {
        "workflow-generation": ["workflows:generate"]}
    # policy: the run-facing switch
    assert load_policy(home, [], {"workflows": "generate"}).may_generate_workflow() is True
    assert load_policy(home, [], {}).may_generate_workflow() is False
    # requires-mode rejects the no-op level (catalog is the absence of a requirement)
    _, probs = normalize_capabilities({"workflows": "catalog"}, label="requires", requires=True)
    assert any("workflows must be generate" in p for p in probs)


def test_unsatisfied_requires_names_the_missing_capabilities(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "run-history": RUN_HISTORY})
    lib = read_library_requires(home)
    missing = unsatisfied_requires(["util-authoring", "communication", "run-history"],
                                   {"actions": ["write_util"]}, lib)
    assert missing == {"communication": ["util:discord"], "run-history": ["runs:last"]}
    full = capabilities_for(["util-authoring", "communication", "run-history"], lib)
    assert unsatisfied_requires(["util-authoring", "communication", "run-history"],
                                full, lib) == {}


# ------------------------------------------------------------------ policy derivation


def test_policy_enforces_capabilities_not_docs(tmp_path):
    """Holding a conduct doc unlocks NOTHING by itself — enforcement reads the routine's
    capabilities mapping alone, so a doc-without-capability misconfiguration fails closed."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    docs_only = load_policy(home, ["util-authoring", "communication"], {})
    assert not docs_only.allows_kind("write_util")
    assert "discord" not in docs_only.utils
    assert docs_only.active == ("util-authoring", "communication")   # prose still rides along

    caps_only = load_policy(home, [], {"actions": ["write_util"], "utils": ["discord"],
                                       "confirm": "creations", "runs": "all"})
    assert caps_only.allows_kind("write_util") and caps_only.allows_kind("util")
    assert "discord" in caps_only.utils
    assert caps_only.confirm == "creations" and caps_only.run_history == "all"
    assert caps_only.deny({"kind": "util", "name": "discord"}) is None
    # the library-wide index survives for denial wording regardless of what is enabled
    assert caps_only.gated_utils == {"discord": ("communication",)}
    assert caps_only.kind_sources == {"write_util": ("util-authoring",)}


def test_policy_ignores_ungated_kinds_in_capabilities(tmp_path):
    home = _lib(tmp_path, {})
    policy = load_policy(home, [], {"actions": ["util", "read_file", "memory_read"]})
    assert policy.actions == frozenset({"memory_read"})   # base kinds are never gated
    assert policy.allows_kind("util") and policy.allows_kind("read_file")


def test_needs_confirm_semantics():
    always = GrantPolicy(actions=frozenset(["write_util"]), confirm="always")
    creations = GrantPolicy(actions=frozenset(["write_util"]), confirm="creations")
    never = GrantPolicy(actions=frozenset(["write_util"]), confirm="never")
    assert always.needs_confirm(creating=True) and always.needs_confirm(creating=False)
    assert creations.needs_confirm(creating=True) and not creations.needs_confirm(creating=False)
    assert not never.needs_confirm(creating=True) and not never.needs_confirm(creating=False)


# ------------------------------------------------------------------ denial messages


BACKGROUND_TASKS = """---
tags: [conversation, background, delegation]
requires:
  actions: [detach]
---
# permission: background tasks — launch long jobs that outlive a reply
body
"""


def test_detach_is_gated_and_denial_names_background_tasks(tmp_path):
    home = _lib(tmp_path, {"background-tasks": BACKGROUND_TASKS})
    none = load_policy(home, [], {})
    denial = none.deny({"kind": "detach", "prompt": "scrape"})
    assert denial and "background-tasks" in denial
    granted = load_policy(home, ["background-tasks"], {"actions": ["detach"]})
    assert granted.deny({"kind": "detach", "prompt": "scrape"}) is None


def test_deny_names_the_covering_permission(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    policy = load_policy(home, [], {})
    denial = policy.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert denial and "util-authoring" in denial and "ask_user" in denial
    denial_util = policy.deny({"kind": "util", "name": "discord", "args": ["send", "hi"]})
    assert denial_util and "communication" in denial_util and "reserved" in denial_util
    # ungated capabilities pass silently
    assert policy.deny({"kind": "util", "name": "websearch"}) is None
    assert policy.deny({"kind": "read_file", "path": "LEDGER.md"}) is None


def test_deny_gates_previous_runs_but_not_the_live_run():
    none = GrantPolicy(current_run_ts="20260712-090000")
    denial = none.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"})
    assert denial and "run-history" in denial
    # the live run's own tree (archived history) stays readable — the engine points there
    assert none.deny({"kind": "read_file",
                      "path": "runs/20260712-090000/history/INDEX.md"}) is None
    # runs/ is never writable, not even with full history access
    full = GrantPolicy(run_history="all")
    assert full.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"}) is None
    w = full.deny({"kind": "write_file", "path": "runs/20260101-000000/x.md", "content": "x"})
    assert w and "read-only" in w
    # a batched read is gated per path — one gated entry denies the whole action
    batched = none.deny({"kind": "read_file",
                         "paths": ["state/a.md", "runs/20260101-000000/result.md"]})
    assert batched and "run-history" in batched
    assert none.deny({"kind": "read_file", "paths": ["state/a.md", "LEDGER.md"]}) is None


def test_deny_gates_edit_file_like_write_file():
    none = GrantPolicy()
    denial = none.deny({"kind": "edit_file", "path": "main.md", "anchor": "a", "replacement": "b"})
    assert denial and "routine-improver" in denial
    w = none.deny({"kind": "edit_file", "path": "runs/20260101-000000/x.md", "anchor": "a"})
    assert w and "read-only" in w
    assert none.deny({"kind": "edit_file", "path": "state/notes.md", "anchor": "a"}) is None


def test_deny_blocks_own_recipe_and_config_writes():
    """Own recipe writes (main.md/stages/traits/tuning.yaml) are a FIXED rule, unlocked
    only via recipe_unlocked (a user fs_write_root covering the dir). routine.yaml is
    config: denied for EVERYONE — the denial routes machine-tunable knobs to tuning.yaml."""
    none = GrantPolicy()
    for path in ("main.md", "stages/collect.md", "traits/ask-policy.md", "./main.md",
                 "tuning.yaml", "routine.yaml"):
        denial = none.deny({"kind": "write_file", "path": path, "content": "x"})
        assert denial and "routine-improver" in denial, path
        assert none.deny({"kind": "read_file", "path": path}) is None, path
    # instruction.md is no longer a recipe file (the seed isn't persisted) — writes are open
    assert none.deny({"kind": "write_file", "path": "instruction.md", "content": "x"}) is None
    # non-recipe writes stay open
    assert none.deny({"kind": "write_file", "path": "state/notes.md", "content": "x"}) is None
    assert none.deny({"kind": "write_file", "path": "LEDGER.md", "content": "x"}) is None
    unlocked = GrantPolicy(recipe_unlocked=True)
    assert unlocked.deny({"kind": "write_file", "path": "main.md", "content": "x"}) is None
    assert unlocked.deny({"kind": "write_file", "path": "tuning.yaml", "content": "x"}) is None
    # …but routine.yaml stays denied even when the recipe is unlocked (config ≠ recipe)
    assert unlocked.deny({"kind": "write_file", "path": "routine.yaml", "content": "x"}) is not None


def test_validate_action_carries_capability_denials():
    """The capability check rides the same retry cycle as the workflow allowlist; finish is
    always permitted and grants=None means unrestricted."""
    from rsched.engine.actions import validate_action

    policy = GrantPolicy(active=("run-history",),
                         gated_utils={"discord": ("communication",)},
                         kind_sources={"write_util": ("util-authoring",)})
    wu = {"say": "s", "kind": "write_util", "name": "x", "content": "# script"}
    problems = validate_action(wu, grants=policy)
    assert len(problems) == 1 and "util-authoring" in problems[0]
    problems2 = validate_action({"say": "s", "kind": "util", "name": "discord"}, grants=policy)
    assert len(problems2) == 1 and "communication" in problems2[0]
    fin = {"say": "s", "kind": "finish", "status": "ok", "summary": "d"}
    assert validate_action(fin, grants=policy) == []
    assert validate_action(wu, grants=None) == []
    # the workflow allowlist still wins first — its message names the permitted kinds
    problems3 = validate_action(wu, allowed_kinds={"read_file"}, grants=policy)
    assert len(problems3) == 1 and "not available" in problems3[0]


def test_lint_flags_bad_requires():
    from rsched.workflows.lint import lint_permission_text, lint_trait_text

    bad = ("---\ntags: [a, b, c]\nrequires:\n  actions: [dance]\n  runs: maybe\n---\n"
           "# permission: x — y\n\nlong enough body\nmore\n")
    problems = lint_permission_text(bad, filename="x.md")
    text = " | ".join(problems)
    assert "not an action kind" in text and "runs must be" in text
    good = ("---\ntags: [a, b, c]\nrequires:\n  actions: [write_util]\n---\n"
            "# permission: x — y\n\nlong enough body\nmore\n")
    assert lint_permission_text(good, filename="x.md") == []
    # a permission without requires is an error; the legacy grants: key is called out;
    # a trait WITH either key is an error
    no_req = "---\ntags: [a, b, c]\n---\n# permission: x — y\n\nbody\nmore\nlines\n"
    assert any("requires" in p for p in lint_permission_text(no_req, filename="x.md"))
    legacy = ("---\ntags: [a, b, c]\ngrants:\n  actions: [write_util]\n---\n"
              "# permission: x — y\n\nbody\nmore\nlines\n")
    assert any("renamed" in p for p in lint_permission_text(legacy, filename="x.md"))
    trait_with_req = ("---\ntags: [a, b, c]\nrequires:\n  utils: [discord]\n---\n"
                      "# trait: x — y\n\nbody\nmore\nlines\n")
    assert any("must not carry" in p
               for p in lint_trait_text(trait_with_req, filename="x.md"))


def test_memory_kinds_are_gated_and_denials_name_the_permission():
    none = GrantPolicy()
    denial = none.deny({"kind": "memory_write", "name": "x"})
    assert denial and "memory" in denial            # names the canonical covering doc
    assert none.deny({"kind": "memory_read", "name": "x"})
    granted = GrantPolicy(actions=frozenset({"memory_read", "memory_write"}))
    assert granted.deny({"kind": "memory_write", "name": "x"}) is None
    assert granted.deny({"kind": "memory_read", "name": "x"}) is None
