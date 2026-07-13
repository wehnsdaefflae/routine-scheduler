"""Permission grants: schema normalization, library-only authority, policy derivation,
and the per-kind denial messages validate_action surfaces."""

from __future__ import annotations

from pathlib import Path

from rsched.grants import (CONFIRM_LEVELS, GrantPolicy, load_policy, normalize_grants,
                           read_library_grants)


def _lib(tmp_path: Path, permissions: dict[str, str]) -> Path:
    home = tmp_path / "library" / "permissions"
    home.mkdir(parents=True, exist_ok=True)
    for slug, text in permissions.items():
        (home / f"{slug}.md").write_text(text, encoding="utf-8")
    return home


AUTHORING = """---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]
  confirm: true
---
# permission: util authoring — create and revise utils
body
"""

AUTONOMOUS = """---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]
  confirm: revisions-only
---
# permission: util authoring autonomous — revisions without approval
body
"""

COMMUNICATION = """---
tags: [communication, policy, notification]
grants:
  utils: [discord]
---
# permission: communication — Discord as a second decision surface
body
"""

RUN_HISTORY = """---
tags: [history, record-keeping, self-management]
grants:
  runs: last
---
# permission: run history — read the previous run
body
"""

RUN_HISTORY_FULL = """---
tags: [history, record-keeping, self-management]
grants:
  runs: all
---
# permission: run history full — read all previous runs
body
"""

SELF_MOD = """---
tags: [self-management, improvement, recipe]
grants:
  self_modify: true
---
# permission: self-modification — refine own recipe
body
"""


# ------------------------------------------------------------------ normalize_grants


def test_normalize_grants_accepts_the_schema():
    g, problems = normalize_grants({"actions": ["util", "write_util"],
                                    "utils": ["discord"], "confirm": True})
    assert problems == []
    assert g == {"actions": ["util", "write_util"], "utils": ["discord"], "confirm": "always"}
    assert normalize_grants({"confirm": "revisions-only"})[0] == {"confirm": "creations"}
    assert normalize_grants({"confirm": False})[0] == {"confirm": "never"}
    assert normalize_grants({"runs": "last"})[0] == {"runs": "last"}
    assert normalize_grants({"runs": "all"})[0] == {"runs": "all"}
    assert normalize_grants(None) == ({}, [])


def test_normalize_grants_reports_and_drops_invalid_parts():
    g, problems = normalize_grants({"actions": ["util", "dance"], "utils": ["Not A Slug"],
                                    "confirm": "sometimes", "shell": True,
                                    "runs": "some", "self_modify": True})
    text = " | ".join(problems)
    assert "'dance' is not an action kind" in text
    assert "'Not A Slug' is not a kebab-case util name" in text
    assert "confirm must be true, false or revisions-only" in text
    assert "grants.shell: unknown key" in text
    assert "runs must be last or all" in text
    assert "grants.self_modify: unknown key" in text   # the grant is retired
    assert g == {"actions": ["util"], "utils": []}      # invalid entries dropped, valid kept
    assert normalize_grants("write_util")[1]            # non-mapping → problem
    bad_list, problems2 = normalize_grants({"actions": "util"})
    assert bad_list == {} and any("must be a list" in p for p in problems2)


# ------------------------------------------------------------------ library authority


def test_grants_read_from_library_only(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "plain": "# permission: plain — no grants\nbody\n"})
    lib = read_library_grants(home)
    assert set(lib) == {"util-authoring", "communication"}   # grant-less docs omitted
    assert lib["util-authoring"]["confirm"] == "always"

    # nothing under a routine dir is ever consulted: holding an unrelated permission
    # changes nothing about write_util
    policy = load_policy(home, ["communication"])
    assert not policy.allows_kind("write_util")
    assert read_library_grants(tmp_path / "nowhere") == {}   # missing library → no grants


def test_broken_frontmatter_degrades_to_no_grants(tmp_path):
    home = _lib(tmp_path, {"broken": "---\ngrants: [not: closed\n---\n# permission: broken — x\n"})
    assert read_library_grants(home) == {}


# ------------------------------------------------------------------ policy derivation


def test_policy_unions_active_grants_and_indexes_the_library(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    policy = load_policy(home, ["util-authoring", "communication", "ghost"])
    assert policy.allows_kind("write_util") and policy.allows_kind("util")
    assert "discord" in policy.utils
    assert policy.deny({"kind": "util", "name": "discord"}) is None
    assert policy.confirm == "always"

    inactive = load_policy(home, ["run-history"])
    assert not inactive.allows_kind("write_util")
    assert inactive.gated_utils == {"discord": ("communication",)}   # library-wide index survives
    assert inactive.kind_sources == {"write_util": ("util-authoring",)}


def test_confirm_most_permissive_active_grant_wins(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "auto": AUTONOMOUS})
    assert load_policy(home, ["util-authoring"]).confirm == "always"
    assert load_policy(home, ["auto"]).confirm == "creations"
    assert load_policy(home, ["util-authoring", "auto"]).confirm == "creations"
    assert load_policy(home, []).confirm == "always"                 # moot without the grant
    for level in CONFIRM_LEVELS:
        assert level in ("always", "creations", "never")


def test_needs_confirm_semantics():
    always = GrantPolicy(actions=frozenset(["write_util"]), confirm="always")
    creations = GrantPolicy(actions=frozenset(["write_util"]), confirm="creations")
    never = GrantPolicy(actions=frozenset(["write_util"]), confirm="never")
    assert always.needs_confirm(creating=True) and always.needs_confirm(creating=False)
    assert creations.needs_confirm(creating=True) and not creations.needs_confirm(creating=False)
    assert not never.needs_confirm(creating=True) and not never.needs_confirm(creating=False)


def test_run_history_most_permissive_wins(tmp_path):
    home = _lib(tmp_path, {"run-history": RUN_HISTORY, "run-history-full": RUN_HISTORY_FULL})
    assert load_policy(home, []).run_history == "none"
    assert load_policy(home, ["run-history"]).run_history == "last"
    assert load_policy(home, ["run-history", "run-history-full"]).run_history == "all"


# ------------------------------------------------------------------ denial messages


def test_deny_names_the_granting_permission(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    policy = load_policy(home, ["run-history"])
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
    """Own recipe + routine.yaml writes are a FIXED rule, not a permission: denied for
    everyone, unlocked only via recipe_unlocked (a user fs_write_root covering the dir)."""
    none = GrantPolicy()
    for path in ("main.md", "steps/collect.md", "traits/ask-policy.md", "instruction.md",
                 "./main.md", "routine.yaml"):
        denial = none.deny({"kind": "write_file", "path": path, "content": "x"})
        assert denial and "routine-improver" in denial, path
        assert none.deny({"kind": "read_file", "path": path}) is None, path
    # non-recipe writes stay open
    assert none.deny({"kind": "write_file", "path": "state/notes.md", "content": "x"}) is None
    assert none.deny({"kind": "write_file", "path": "LEDGER.md", "content": "x"}) is None
    unlocked = GrantPolicy(recipe_unlocked=True)
    assert unlocked.deny({"kind": "write_file", "path": "main.md", "content": "x"}) is None
    assert unlocked.deny({"kind": "write_file", "path": "routine.yaml", "content": "x"}) is None


def test_validate_action_carries_grant_denials():
    """The grants check rides the same retry cycle as the workflow allowlist; finish is
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


def test_lint_flags_bad_grants():
    from rsched.workflows.lint import lint_permission_text, lint_trait_text

    bad = ("---\ntags: [a, b, c]\ngrants:\n  actions: [dance]\n  confirm: maybe\n---\n"
           "# permission: x — y\n\nlong enough body\nmore\n")
    problems = lint_permission_text(bad, filename="x.md")
    text = " | ".join(problems)
    assert "not an action kind" in text and "confirm must be" in text
    good = ("---\ntags: [a, b, c]\ngrants:\n  actions: [write_util]\n  confirm: true\n---\n"
            "# permission: x — y\n\nlong enough body\nmore\n")
    assert lint_permission_text(good, filename="x.md") == []
    # a permission without grants is an error; a trait WITH grants is an error
    no_grants = "---\ntags: [a, b, c]\n---\n# permission: x — y\n\nbody\nmore\nlines\n"
    assert any("grants" in p for p in lint_permission_text(no_grants, filename="x.md"))
    trait_with_grants = ("---\ntags: [a, b, c]\ngrants:\n  utils: [discord]\n---\n"
                         "# trait: x — y\n\nbody\nmore\nlines\n")
    assert any("must not carry grants" in p
               for p in lint_trait_text(trait_with_grants, filename="x.md"))


def test_memory_kinds_are_gated_and_denials_name_the_permission():
    none = GrantPolicy()
    denial = none.deny({"kind": "memory_write", "name": "x"})
    assert denial and "memory" in denial            # names the canonical granting permission
    assert none.deny({"kind": "memory_read", "name": "x"})
    granted = GrantPolicy(actions=frozenset({"memory_read", "memory_write"}))
    assert granted.deny({"kind": "memory_write", "name": "x"}) is None
    assert granted.deny({"kind": "memory_read", "name": "x"}) is None
