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
    assert caps == {"actions": ["write_util"], "utils": ["discord"], "util_tags": [],
                    "confirm": "always", "rule_confirm": "always", "runs": "last",
                    "workflows": "catalog"}
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
        "actions": [], "utils": [], "util_tags": [], "confirm": "never",
        "rule_confirm": "always", "runs": "none", "workflows": "catalog"}
    # util-authoring held → write_util survives (with its policy); discord + runs still floored
    assert floor_capabilities(["util-authoring"], lib, orphan) == {
        "actions": ["write_util"], "utils": [], "util_tags": [], "confirm": "never",
        "rule_confirm": "always", "runs": "none", "workflows": "catalog"}
    # run-history held → run DEPTH (a user dial) is kept above none; actions/utils floored
    kept = floor_capabilities(["run-history"], lib, orphan)
    assert kept["runs"] == "all" and kept["actions"] == [] and kept["utils"] == []
    # raise THEN floor == exactly the held docs' requires + policy dials, no contradiction
    active = ["util-authoring", "communication", "run-history"]
    assert floor_capabilities(active, lib, capabilities_for(active, lib)) == {
        "actions": ["write_util"], "utils": ["discord"], "util_tags": [], "confirm": "always",
        "rule_confirm": "always", "runs": "last", "workflows": "catalog"}


def test_floor_keeps_gated_kind_via_default_source_when_doc_predates_it(tmp_path):
    """Regression (a toggle reverting on save): a gated kind whose permission doc's requires:
    predates the kind must still persist when the user EXPLICITLY opts in AND holds the
    canonical source permission (_DEFAULT_KIND_SOURCE). Otherwise floor_capabilities strips
    it every save. Shown here with write_rule, whose canonical source is rule-authoring."""
    authoring_no_rule = AUTHORING.replace("actions: [write_util]", "actions: [write_util]")
    home = _lib(tmp_path, {"util-authoring": authoring_no_rule,
                           "rule-authoring": AUTHORING.replace(
                               "actions: [write_util]", "actions: []")})
    lib = read_library_requires(home)
    opt_in = {"actions": ["write_util", "write_rule"]}
    # rule-authoring held + explicit opt-in → write_rule survives via the canonical source
    assert floor_capabilities(["util-authoring", "rule-authoring"], lib, opt_in)["actions"] == \
        ["write_util", "write_rule"]
    # not held → floored away entirely
    assert floor_capabilities([], lib, opt_in)["actions"] == []
    # RAISE is unchanged: merely holding util-authoring does NOT auto-add anything else
    assert capabilities_for(["util-authoring"], lib)["actions"] == ["write_util"]


def test_util_authoring_no_longer_carries_deletion(tmp_path):
    """0.226.0: remove_util's canonical source is util-removal, not util-authoring. Holding
    only util-authoring must NOT float an explicit remove_util past the floor — while the two
    were fused, every routine allowed to create a util could also delete one."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING})
    lib = read_library_requires(home)
    opt_in = {"actions": ["write_util", "remove_util"]}
    assert floor_capabilities(["util-authoring"], lib, opt_in)["actions"] == ["write_util"]


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
    # policy: the run-facing switch
    assert load_policy(home, [], {"workflows": "generate"}).may_generate_workflow() is True
    assert load_policy(home, [], {}).may_generate_workflow() is False
    # requires-mode rejects the no-op level (catalog is the absence of a requirement)
    _, probs = normalize_capabilities({"workflows": "catalog"}, label="requires", requires=True)
    assert any("workflows must be generate" in p for p in probs)


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


def test_run_history_floors_at_last_for_every_routine(tmp_path):
    """D96 (user decision 2026-08-20): own-runs read at 'last' depth is ALWAYS ON — a
    routine policy never comes out of load_policy below 'last', whatever the saved caps
    say; only 'all' remains permission-governed. The loop's depth>0 seam drops children
    back to 'none' (a child's brief, not the archive, is its context)."""
    home = _lib(tmp_path, {})
    assert load_policy(home, [], {}).run_history == "last"                    # no caps
    assert load_policy(home, [], {"runs": "none"}).run_history == "last"      # explicit none
    assert load_policy(home, [], {"runs": "last"}).run_history == "last"
    assert load_policy(home, [], {"runs": "all"}).run_history == "all"        # opt-in kept


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


def test_subrun_denial_names_the_child_scope_not_the_routine(tmp_path):
    """R46: a spawned/subtask child runs with capabilities OFF by design, so a gated-kind
    denial must attribute the limit to the child sub-workflow (and route to the parent),
    never claim the routine lacks the capability — which misled a parent that DOES hold it."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING})
    from dataclasses import replace
    base = load_policy(home, [], {})
    child = replace(base, is_subrun=True)
    d_child = child.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert d_child and "child sub-workflow" in d_child and "PARENT" in d_child
    assert "this routine's capabilities" not in d_child
    # a normal (non-subrun) policy keeps the routine-scoped wording + the ask_user route
    d_routine = base.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert d_routine and "this routine's capabilities" in d_routine and "ask_user" in d_routine


def test_deny_gates_previous_runs_but_not_the_live_run():
    none = GrantPolicy(current_run_ts="20260712-090000")
    denial = none.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"})
    assert denial and "not readable in this scope" in denial   # post-D96: child-scope copy
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
    assert batched and "not readable in this scope" in batched
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
    for path in ("main.md", "stages/collect.md", "./main.md",
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
    from rsched.workflows.lint import lint_permission_text, lint_rule_text

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
                      "# rule: x — y\n\nbody\nmore\nlines\n")
    assert any("must not carry" in p
               for p in lint_rule_text(trait_with_req, filename="x.md"))


def test_memory_kinds_are_gated_and_denials_name_the_permission():
    none = GrantPolicy()
    denial = none.deny({"kind": "memory_write", "name": "x"})
    assert denial and "memory" in denial            # names the canonical covering doc
    assert none.deny({"kind": "memory_read", "name": "x"})
    granted = GrantPolicy(actions=frozenset({"memory_read", "memory_write"}))
    assert granted.deny({"kind": "memory_write", "name": "x"}) is None
    assert granted.deny({"kind": "memory_read", "name": "x"}) is None


def test_admin_lifts_capability_gating_only(tmp_path):
    """D62: an admin conversation leg lifts CAPABILITY gating (gated kinds, reserved utils,
    previous-run read depth) but leaves every STRUCTURAL / ownership gate in force."""
    # A stock (no-capability) policy denies gated kinds + reserved utils; its admin twin allows.
    lib = _lib(tmp_path, {"util-authoring": AUTHORING, "communication": COMMUNICATION,
                          "run-history": RUN_HISTORY})
    base = load_policy(lib, [], None, current_run_ts="20260712-090000")
    admin = load_policy(lib, [], None, current_run_ts="20260712-090000", admin=True)

    # capability gates: OFF for base, LIFTED for admin
    assert not base.allows_kind("write_util") and admin.allows_kind("write_util")
    assert base.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert admin.deny({"kind": "write_util", "name": "x", "content": "y"}) is None
    assert base.deny({"kind": "util", "name": "discord", "args": ["send", "hi"]})
    assert admin.deny({"kind": "util", "name": "discord", "args": ["send", "hi"]}) is None
    # previous-run READ depth: post-D96 a routine floors at 'last', so deny() passes the
    # read for base too (depth enforcement lives in fileops' read gate); only a scope
    # WITHOUT history — a child — still refuses here, and admin lifts even that
    from dataclasses import replace
    child = replace(base, run_history="none")
    assert child.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"})
    assert base.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"}) is None
    assert admin.deny({"kind": "read_file", "path": "runs/20260101-000000/result.md"}) is None

    # STRUCTURAL gates STILL apply under admin — these are NOT capabilities:
    #  - runs/ stays engine-owned / write-protected
    w = admin.deny({"kind": "write_file", "path": "runs/20260101-000000/x.md", "content": "x"})
    assert w and "read-only" in w
    #  - the routine's own recipe stays sealed (admin ≠ recipe_unlocked)
    r = admin.deny({"kind": "write_file", "path": "main.md", "content": "x"})
    assert r and "routine-improver" in r
    #  - routine.yaml config is the user's, denied for everyone including admin
    c = admin.deny({"kind": "write_file", "path": "routine.yaml", "content": "x"})
    assert c is not None


# ------------------------------------------------------- util TAG classes (fail-closed)

MESSAGING = """---
tags: [communication, policy]
requires:
  utils: [discord]
  util_tags: [messaging]
---
# permission: communication — chat channels
body
"""


def _util(lib_home: Path, name: str, tags: str) -> None:
    """A minimal catalog entry beside the permissions dir (list_utils reads <home>/utils)."""
    d = lib_home.parent / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        f'"""{name} — a test util.\n\nusage: gu {name}\ncalls: (none)\ntags: {tags}\n"""\n',
        encoding="utf-8")


def test_util_tags_accepted_in_requires_and_capabilities():
    req, problems = normalize_capabilities({"util_tags": ["messaging"]}, requires=True)
    assert req == {"util_tags": ["messaging"]} and not problems
    caps, problems = normalize_capabilities({"util_tags": ["messaging"]})
    assert caps == {"util_tags": ["messaging"]} and not problems
    # a tag must be a lowercase non-empty string; junk is dropped AND reported
    caps, problems = normalize_capabilities({"util_tags": ["Messaging", "", 3]})
    assert caps == {"util_tags": []} and len(problems) == 3


def test_a_tag_gate_closes_every_util_carrying_that_tag(tmp_path):
    home = _lib(tmp_path, {"communication": MESSAGING})
    _util(home, "signal", "signal, messaging, send")
    _util(home, "page-fetch", "web, fetch")          # untagged by the gate → stays open

    ungranted = load_policy(home, [], {})
    denial = ungranted.deny({"kind": "util", "name": "signal", "args": ["send"]})
    assert denial and "communication" in denial and "util:signal" in denial
    # an ungated util is untouched by the tag gate
    assert ungranted.deny({"kind": "util", "name": "page-fetch", "args": ["x"]}) is None

    # holding the CLASS covers the util without naming it
    granted = load_policy(home, ["communication"], {"util_tags": ["messaging"]})
    assert granted.deny({"kind": "util", "name": "signal", "args": ["send"]}) is None
    # so does the by-name grant, unchanged
    by_name = load_policy(home, ["communication"], {"utils": ["signal"]})
    assert by_name.deny({"kind": "util", "name": "signal", "args": ["send"]}) is None


def test_a_new_util_carrying_a_gated_tag_is_closed_by_default(tmp_path):
    """The point of the tag gate: the library gaining a util must not open a hole."""
    home = _lib(tmp_path, {"communication": MESSAGING})
    _util(home, "signal", "signal, messaging, send")
    granted = load_policy(home, ["communication"], {"utils": ["signal"]})  # named, not classed
    _util(home, "matrix", "matrix, messaging, send")                      # library gains one
    fresh = load_policy(home, ["communication"], {"utils": ["signal"]})
    assert fresh.deny({"kind": "util", "name": "matrix", "args": ["send"]})
    assert granted.deny({"kind": "util", "name": "signal", "args": ["send"]}) is None
    # the class grant covers the newcomer with no config change
    classed = load_policy(home, ["communication"], {"util_tags": ["messaging"]})
    assert classed.deny({"kind": "util", "name": "matrix", "args": ["send"]}) is None


def test_no_tag_gate_in_the_library_means_no_catalog_read(tmp_path):
    """With no doc declaring util_tags the policy is byte-identical to the name-only one."""
    home = _lib(tmp_path, {"communication": COMMUNICATION})
    _util(home, "signal", "signal, messaging, send")
    policy = load_policy(home, [], {})
    assert policy.util_tag_index == {}
    assert policy.deny({"kind": "util", "name": "signal", "args": ["send"]}) is None


def test_tag_class_survives_the_raise_then_floor_round_trip(tmp_path):
    home = _lib(tmp_path, {"communication": MESSAGING})
    lib = read_library_requires(home)
    raised = capabilities_for(["communication"], lib)
    assert raised["util_tags"] == ["messaging"]
    assert floor_capabilities(["communication"], lib, raised)["util_tags"] == ["messaging"]
    # dropping the permission floors the class away — no orphan capability
    assert floor_capabilities([], lib, raised)["util_tags"] == []


REVISION = """---
tags: [tool-use, utils, authoring]
requires:
  actions: [revise_util]
---
# permission: util revision — change an existing util
body
"""

SIGNAL_DOC = """---
tags: [communication, messaging, outbound]
requires:
  utils: [signal]
---
# permission: signal messaging
body
"""


def _write_util(name: str) -> dict:
    return {"kind": "write_util", "name": name, "content": "x"}


def test_write_util_splits_create_from_revise(tmp_path):
    """One action kind, two permissions: the engine decides which act this is from whether
    the target already exists, so the model never has to know before it looks."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "util-revision": REVISION})
    creator = load_policy(home, ["util-authoring"], {"actions": ["write_util"]})
    reviser = load_policy(home, ["util-revision"], {"actions": ["revise_util"]})
    # the catalog is empty here, so every name reads as NEW
    assert creator.deny(_write_util("brand-new")) is None
    denial = reviser.deny(_write_util("brand-new"))
    assert denial and "CREATION" in denial and "util-authoring" in denial
    # both halves held → neither branch can refuse
    both = load_policy(home, ["util-authoring", "util-revision"],
                       {"actions": ["write_util", "revise_util"]})
    assert both.deny(_write_util("brand-new")) is None
    # neither half → the kind is off entirely
    assert load_policy(home, [], {}).deny(_write_util("brand-new"))


def test_write_util_revise_branch_uses_the_live_catalog(tmp_path):
    """An EXISTING name is a revision, so the create-only holder is refused and the
    revise-only holder is allowed — the mirror image of the create case."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "util-revision": REVISION})
    (home.parent / "utils" / "existing").mkdir(parents=True)
    (home.parent / "utils" / "existing" / "main.py").write_text(
        '"""does a thing.\n\ntags: a, b, c\nsecrets: (none)\ncalls: (none)\nnet: none\n'
        'usage: gu existing\n"""\n', encoding="utf-8")
    creator = load_policy(home, ["util-authoring"], {"actions": ["write_util"]})
    reviser = load_policy(home, ["util-revision"], {"actions": ["revise_util"]})
    assert "existing" in creator.known_utils
    denial = creator.deny(_write_util("existing"))
    assert denial and "REVISION" in denial and "util-revision" in denial
    assert reviser.deny(_write_util("existing")) is None
    # …and the create-only holder can still create
    assert creator.deny(_write_util("not-there-yet")) is None


def test_util_grant_can_be_scoped_to_one_verb(tmp_path):
    """`signal:read` grants exactly that subcommand — a read-only channel is not a write one."""
    home = _lib(tmp_path, {"messaging-signal": SIGNAL_DOC})
    ro = load_policy(home, ["messaging-signal"], {"utils": ["signal:read"]})
    assert ro.deny({"kind": "util", "name": "signal", "args": ["read", "--limit", "5"]}) is None
    denial = ro.deny({"kind": "util", "name": "signal", "args": ["send", "hi"]})
    assert denial and "read" in denial and "signal" in denial
    # a call with no verb at all cannot be matched against the scope → refused
    assert ro.deny({"kind": "util", "name": "signal", "args": []})
    # the bare grant still covers every verb
    full = load_policy(home, ["messaging-signal"], {"utils": ["signal"]})
    assert full.deny({"kind": "util", "name": "signal", "args": ["send", "hi"]}) is None


def test_verb_scoped_grant_survives_the_floor_and_stays_gated(tmp_path):
    """A narrower grant survives a doc that reserves the whole util; and a doc reserving
    only a verb still makes the util gated (the fail-open direction)."""
    home = _lib(tmp_path, {"messaging-signal": SIGNAL_DOC})
    lib = read_library_requires(home)
    floored = floor_capabilities(["messaging-signal"], lib,
                                 {**EMPTY_CAPABILITIES, "utils": ["signal:read"]})
    assert floored["utils"] == ["signal:read"]
    # unheld doc → the scoped entry is floored away like any other
    assert floor_capabilities([], lib, {**EMPTY_CAPABILITIES,
                                        "utils": ["signal:read"]})["utils"] == []
    # a doc that reserves ONLY `signal:read` still gates the `signal` util by bare name
    verb_only = _lib(tmp_path / "v", {"ro": SIGNAL_DOC.replace("utils: [signal]",
                                                              "utils: [signal:read]")})
    pol = load_policy(verb_only, [], {})
    assert "signal" in pol.gated_utils
    assert pol.deny({"kind": "util", "name": "signal", "args": ["send"]})
