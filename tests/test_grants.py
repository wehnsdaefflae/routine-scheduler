"""Fragment grants: schema normalization, library-only authority, policy derivation,
and the per-kind denial messages validate_action surfaces."""

from __future__ import annotations

from pathlib import Path

from rsched.grants import (CONFIRM_LEVELS, GrantPolicy, load_policy, normalize_grants,
                           read_library_grants)


def _lib(tmp_path: Path, fragments: dict[str, str]) -> Path:
    home = tmp_path / "library" / "fragments"
    home.mkdir(parents=True, exist_ok=True)
    for slug, text in fragments.items():
        (home / f"{slug}.md").write_text(text, encoding="utf-8")
    return home


AUTHORING = """---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]
  confirm: true
---
# fragment: util authoring — create and revise utils
body
"""

AUTONOMOUS = """---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]
  confirm: revisions-only
---
# fragment: util authoring autonomous — revisions without approval
body
"""

COMMUNICATION = """---
tags: [communication, policy, notification]
grants:
  utils: [discord]
---
# fragment: communication — Discord for blocking questions
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
    assert normalize_grants(None) == ({}, [])


def test_normalize_grants_reports_and_drops_invalid_parts():
    g, problems = normalize_grants({"actions": ["util", "dance"], "utils": ["Not A Slug"],
                                    "confirm": "sometimes", "shell": True})
    text = " | ".join(problems)
    assert "'dance' is not an action kind" in text
    assert "'Not A Slug' is not a kebab-case util name" in text
    assert "confirm must be true, false or revisions-only" in text
    assert "grants.shell: unknown key" in text
    assert g == {"actions": ["util"], "utils": []}      # invalid entries dropped, valid kept
    assert normalize_grants("write_util")[1]            # non-mapping → problem
    bad_list, problems2 = normalize_grants({"actions": "util"})
    assert bad_list == {} and any("must be a list" in p for p in problems2)


# ------------------------------------------------------------------ library authority


def test_grants_read_from_library_only(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                           "plain": "# fragment: plain — no grants\nbody\n"})
    lib = read_library_grants(home)
    assert set(lib) == {"util-authoring", "communication"}   # grant-less fragments omitted
    assert lib["util-authoring"]["confirm"] == "always"

    # the routine-local copy is NEVER consulted: a self-granted local edit changes nothing
    policy = load_policy(home, ["communication"])
    assert not policy.allows_kind("write_util")
    assert read_library_grants(tmp_path / "nowhere") == {}   # missing library → no grants


def test_broken_frontmatter_degrades_to_no_grants(tmp_path):
    home = _lib(tmp_path, {"broken": "---\ngrants: [not: closed\n---\n# fragment: broken — x\n"})
    assert read_library_grants(home) == {}


# ------------------------------------------------------------------ policy derivation


def test_policy_unions_active_grants_and_indexes_the_library(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    policy = load_policy(home, ["util-authoring", "communication", "ghost"])
    assert policy.allows_kind("write_util") and policy.allows_kind("util")
    assert "discord" in policy.utils
    assert policy.deny({"kind": "util", "name": "discord"}) is None
    assert policy.confirm == "always"

    inactive = load_policy(home, ["ledger-discipline"])
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


# ------------------------------------------------------------------ denial messages


def test_deny_names_the_granting_fragment(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION})
    policy = load_policy(home, ["ledger-discipline"])
    denial = policy.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert denial and "util-authoring" in denial and "ask_user" in denial
    denial_util = policy.deny({"kind": "util", "name": "discord", "args": ["send", "hi"]})
    assert denial_util and "communication" in denial_util and "reserved" in denial_util
    # ungated capabilities pass silently
    assert policy.deny({"kind": "util", "name": "websearch"}) is None
    assert policy.deny({"kind": "read_file", "path": "LEDGER.md"}) is None


def test_validate_action_carries_grant_denials():
    """The grants check rides the same retry cycle as the workflow allowlist; finish is
    always permitted and grants=None means unrestricted."""
    from rsched.engine.actions import validate_action

    policy = GrantPolicy(active=("ledger-discipline",),
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
    from rsched.workflows.lint import lint_fragment_text

    bad = ("---\ntags: [a, b, c]\ngrants:\n  actions: [dance]\n  confirm: maybe\n---\n"
           "# fragment: x — y\n\nlong enough body\nmore\n")
    problems = lint_fragment_text(bad, filename="x.md")
    text = " | ".join(problems)
    assert "not an action kind" in text and "confirm must be" in text
    good = ("---\ntags: [a, b, c]\ngrants:\n  actions: [util, write_util]\n  confirm: true\n---\n"
            "# fragment: x — y\n\nlong enough body\nmore\n")
    assert lint_fragment_text(good, filename="x.md") == []
