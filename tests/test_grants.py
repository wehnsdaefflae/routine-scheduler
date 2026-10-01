"""The two-layer permission set: capabilities normalization, the requires: library index,
activation/deactivation cascades, policy derivation from the routine's OWN capabilities,
and the per-kind denial messages validate_action surfaces."""

from __future__ import annotations

from pathlib import Path

from rsched.grantpolicy import GrantPolicy
from rsched.grants import (
    EMPTY_CAPABILITIES,
    capabilities_for,
    floor_capabilities,
    normalize_capabilities,
    read_library_requires,
)
from rsched.policyload import load_policy


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
# permission: discord messaging — reach a person on Discord
body
"""



# ------------------------------------------------------------- normalize_capabilities


def test_normalize_capabilities_accepts_the_schema():
    c, problems = normalize_capabilities({"actions": ["shell", "write_util"],
                                          "utils": ["discord"], "confirm": "always"})
    assert problems == []
    assert c == {"actions": ["shell", "write_util"], "utils": ["discord"], "confirm": "always"}
    # only the canonical vocabulary is accepted — legacy true/false/revisions-only is gone
    for legacy in (True, False, "revisions-only"):
        got, probs = normalize_capabilities({"confirm": legacy})
        assert got == {} and any("confirm" in p for p in probs)
    assert normalize_capabilities({"confirm": "creations"})[0] == {"confirm": "creations"}
    assert normalize_capabilities({"runs": "none"})[0] == {"runs": "none"}
    assert normalize_capabilities({"runs": "all"})[0] == {"runs": "all"}
    assert normalize_capabilities(None) == ({}, [])


def test_normalize_capabilities_reports_and_drops_invalid_parts():
    c, problems = normalize_capabilities({"actions": ["shell", "util", "dance", "detach"],
                                          "utils": ["Not A Slug"],
                                          "confirm": "sometimes", "shell": True,
                                          "runs": "some", "self_modify": True})
    text = " | ".join(problems)
    assert "'dance' is not a capability" in text
    # a kind every routine may use needs no switch — naming one is config that says nothing
    assert "'util' is not a capability — every routine may use it" in text
    # …and detach is structural: no config names it
    assert "'detach' is not a capability" in text
    assert "'Not A Slug' is not a kebab-case util name" in text
    assert "confirm must be always, creations or never" in text
    assert "capabilities.shell: unknown key" in text
    assert "runs must be none or last or all" in text
    assert "capabilities.self_modify: unknown key" in text
    assert c == {"actions": ["shell"], "utils": []}     # invalid entries dropped, valid kept
    assert normalize_capabilities("write_util")[1]      # non-mapping → problem
    bad_list, problems2 = normalize_capabilities({"actions": "util"})
    assert bad_list == {} and any("must be a list" in p for p in problems2)


def test_requires_mode_takes_only_actions_and_utils():
    """A doc may not demand a SETTING — an approval level, run-history depth or the reminder
    stores are the user's choice per routine, so a doc naming one is a lint problem."""
    req, problems = normalize_capabilities({"actions": ["write_util"], "confirm": True},
                                           label="requires", requires=True)
    assert req == {"actions": ["write_util"]}
    assert any("requires.confirm: unknown key" in p for p in problems)
    for setting in ({"runs": "all"}, {"reminders": "local"}):
        got, p2 = normalize_capabilities(setting, label="requires", requires=True)
        assert got == {} and any("a setting the user chooses" in p for p in p2)


# ------------------------------------------------------------------ library requires


def test_requires_read_from_library_only(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION,
                           "plain": "# permission: plain — no requires\nbody\n"})
    lib = read_library_requires(home)
    assert set(lib) == {"util-authoring", "messaging-discord"}   # requires-less docs omitted
    assert lib["util-authoring"] == {"actions": ["write_util"]}
    assert read_library_requires(tmp_path / "nowhere") == {}   # missing library → none


def test_broken_frontmatter_degrades_to_no_requires(tmp_path):
    home = _lib(tmp_path, {"broken": "---\nrequires: [not: closed\n---\n# permission: broken — x\n"})
    assert read_library_requires(home) == {}


# ------------------------------------------------------------------------- cascades


def test_a_forever_grant_on_a_setting_writes_the_setting_itself(tmp_path):
    """The four-state model needs all four states. Run-history depth and the reminder stores
    are SETTINGS no doc covers, so allow_forever on such a request writes the level granted
    — without activating any permission — and never lowers one already set."""
    from types import SimpleNamespace

    from rsched.web.grants_apply import _apply_capability

    perms = tmp_path / "permissions"
    perms.mkdir(parents=True)
    server = SimpleNamespace(permissions_home=perms)
    raw: dict = {"permissions": [], "capabilities": {}}
    _apply_capability(server, raw, "reminders", "global")
    assert raw["permissions"] == []
    assert raw["capabilities"]["reminders"] == "global"   # the LEVEL granted
    _apply_capability(server, raw, "runs", "all")
    assert raw["capabilities"]["runs"] == "all"
    _apply_capability(server, raw, "runs", "last")
    assert raw["capabilities"]["runs"] == "all"           # a grant never lowers a setting


def test_capabilities_for_raises_the_base_to_cover_active_docs(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION})
    lib = read_library_requires(home)
    caps = capabilities_for(["util-authoring", "messaging-discord"], lib)
    assert caps == {"actions": ["write_util"], "utils": ["discord"],
                    "confirm": "always", "rule_confirm": "always",
                    "remind_confirm": "always", "runs": "none", "reminders": "none"}
    # the settings pass through untouched: the raise only ever adds means
    base = {"actions": ["shell"], "utils": [], "confirm": "never", "runs": "all"}
    caps2 = capabilities_for(["util-authoring"], lib, base)
    assert caps2["runs"] == "all" and caps2["confirm"] == "never"
    assert caps2["actions"] == ["shell", "write_util"]
    assert capabilities_for([], lib) == EMPTY_CAPABILITIES


def test_floor_capabilities_binds_gated_capabilities_to_held_permissions(tmp_path):
    """D8: a gated action / reserved util survives only as the MEANS of a HELD permission;
    the settings (approval dials, run depth, reminder stores) remain user policy under it.
    raise+floor together == exactly the union of the held docs' requires (plus settings)."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION})
    lib = read_library_requires(home)
    orphan = {"actions": ["write_util"], "utils": ["discord"], "confirm": "never", "runs": "all"}
    # nothing held → every gated capability is floored away; the settings are preserved
    assert floor_capabilities([], lib, orphan) == {
        "actions": [], "utils": [], "confirm": "never",
        "rule_confirm": "always", "remind_confirm": "always", "runs": "all",
        "reminders": "none"}
    # util-authoring held → write_util survives; discord is still floored
    assert floor_capabilities(["util-authoring"], lib, orphan) == {
        "actions": ["write_util"], "utils": [], "confirm": "never",
        "rule_confirm": "always", "remind_confirm": "always", "runs": "all",
        "reminders": "none"}
    # raise THEN floor == exactly the held docs' requires + settings, no contradiction
    active = ["util-authoring", "messaging-discord"]
    assert floor_capabilities(active, lib, capabilities_for(active, lib)) == {
        "actions": ["write_util"], "utils": ["discord"], "confirm": "always",
        "rule_confirm": "always", "remind_confirm": "always", "runs": "none",
        "reminders": "none"}


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


def test_policy_enforces_capabilities_not_docs(tmp_path):
    """Holding a conduct doc unlocks NOTHING by itself — enforcement reads the routine's
    capabilities mapping alone, so a doc-without-capability misconfiguration fails closed."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION})
    docs_only = load_policy(home, ["util-authoring", "messaging-discord"], {})
    assert not docs_only.allows_kind("write_util")
    assert "discord" not in docs_only.utils
    assert docs_only.active == ("util-authoring", "messaging-discord")   # prose still rides along

    caps_only = load_policy(home, [], {"actions": ["write_util"], "utils": ["discord"],
                                       "confirm": "creations", "runs": "all"})
    assert caps_only.allows_kind("write_util") and caps_only.allows_kind("util")
    assert "discord" in caps_only.utils
    assert caps_only.confirm == "creations" and caps_only.run_history == "all"
    assert caps_only.deny({"kind": "util", "name": "discord"}) is None
    # the library-wide index survives for denial wording regardless of what is enabled
    assert caps_only.gated_utils == {"discord": ("messaging-discord",)}
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
    policy = load_policy(home, [], {"actions": ["util", "read_file", "memory_read", "shell"]})
    assert policy.actions == frozenset({"shell"})          # base kinds are never gated
    assert policy.allows_kind("util") and policy.allows_kind("read_file")


def test_needs_confirm_semantics():
    always = GrantPolicy(actions=frozenset(["write_util"]), confirm="always")
    creations = GrantPolicy(actions=frozenset(["write_util"]), confirm="creations")
    never = GrantPolicy(actions=frozenset(["write_util"]), confirm="never")
    assert always.needs_confirm(creating=True) and always.needs_confirm(creating=False)
    assert creations.needs_confirm(creating=True) and not creations.needs_confirm(creating=False)
    assert not never.needs_confirm(creating=True) and not never.needs_confirm(creating=False)


# ------------------------------------------------------------------ denial messages




def test_detach_is_structural_and_its_denial_says_so(tmp_path):
    """No permission grants detach; a root conversation gets it at setup. A denial that named a
    permission would send the run after a switch that does not exist."""
    none = load_policy(tmp_path, [], {})
    denial = none.deny({"kind": "detach", "prompt": "scrape"})
    assert denial and "only in a root conversation" in denial and "permission" not in denial


def test_deny_names_the_covering_permission(tmp_path):
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION})
    policy = load_policy(home, [], {})
    denial = policy.deny({"kind": "write_util", "name": "x", "content": "y"})
    assert denial and "util-authoring" in denial and "ask_user" in denial
    denial_util = policy.deny({"kind": "util", "name": "discord", "args": ["send", "hi"]})
    assert denial_util and "messaging-discord" in denial_util and "reserved" in denial_util
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
    assert denial and "recipe-authoring" in denial
    w = none.deny({"kind": "edit_file", "path": "runs/20260101-000000/x.md", "anchor": "a"})
    assert w and "read-only" in w
    assert none.deny({"kind": "edit_file", "path": "state/notes.md", "anchor": "a"}) is None


def test_deny_blocks_own_recipe_and_config_writes():
    """Own recipe writes (main.md/stages/tuning.yaml) need the `write_recipe` capability, held
    through the recipe-authoring conduct doc (0.261.0). They used to unlock as a side effect of
    an fs_write_root covering the routine dir, which conflated "may write files here" with "may
    reword its own task". routine.yaml is config: denied for EVERYONE — the denial routes
    machine-tunable knobs to tuning.yaml."""
    none = GrantPolicy()
    for path in ("main.md", "stages/collect.md", "./main.md", "tuning.yaml"):
        denial = none.deny({"kind": "write_file", "path": path, "content": "x"})
        assert denial and "recipe-authoring" in denial, path
        assert none.deny({"kind": "read_file", "path": path}) is None, path
    # routine.yaml is CONFIG, not recipe: its denial is absolute and names no permission,
    # because no capability unlocks it — not recipe-authoring, not admin
    cfg_denial = none.deny({"kind": "write_file", "path": "routine.yaml", "content": "x"})
    assert cfg_denial and "recipe-authoring" not in cfg_denial
    assert "would change routine config" in cfg_denial
    assert none.deny({"kind": "read_file", "path": "routine.yaml"}) is None
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


def test_the_recipe_denial_names_the_request_route_once():
    """The request route is a whole sentence of its own ("If it is essential, request it:
    ask_user with request: …"). The recipe denial prefixed it with ", or request it: " and the
    model read "or request it: If it is essential, request it: ask_user …" on every refusal."""
    from rsched.grantpolicy import REQUEST_ROUTE_MARK

    denial = GrantPolicy().deny({"kind": "edit_file", "path": "stages/collect.md"})
    assert denial is not None and denial.count("request it:") == 1
    assert f'{REQUEST_ROUTE_MARK} "action:write_recipe"' in denial
    assert "(or a report). If it is essential" in denial


def test_validate_action_carries_capability_denials():
    """The capability check rides the same retry cycle as the workflow allowlist; finish is
    always permitted and grants=None means unrestricted."""
    from rsched.engine.actions import validate_action

    policy = GrantPolicy(active=("messaging-discord",),
                         gated_utils={"discord": ("messaging-discord",)},
                         kind_sources={"write_util": ("util-authoring",)})
    wu = {"say": "s", "kind": "write_util", "name": "x", "content": "# script"}
    problems = validate_action(wu, grants=policy)
    assert len(problems) == 1 and "util-authoring" in problems[0]
    problems2 = validate_action({"say": "s", "kind": "util", "name": "discord"}, grants=policy)
    assert len(problems2) == 1 and "messaging-discord" in problems2[0]
    fin = {"say": "s", "kind": "finish", "status": "ok", "summary": "d"}
    assert validate_action(fin, grants=policy) == []
    assert validate_action(wu, grants=None) == []
    # the workflow allowlist still wins first — its message names the permitted kinds
    problems3 = validate_action(wu, allowed_kinds={"read_file"}, grants=policy)
    assert len(problems3) == 1 and "not available" in problems3[0]


def test_lint_flags_bad_requires():
    from rsched.workflows.lint import lint_permission_text, lint_rule_text

    bad = ("---\ntags: [a, b, c]\nrequires:\n  actions: [dance]\n  runs: all\n---\n"
           "# permission: x — y\n\nlong enough body\nmore\n")
    problems = lint_permission_text(bad, filename="x.md")
    text = " | ".join(problems)
    assert "not a capability" in text and "requires.runs: unknown key" in text
    good = ("---\ntags: [a, b, c]\n"
            "effect:\n  with: write a util every routine can then call\n"
            "  without: uses only the utils that already exist\n"
            "  when: it keeps needing a tool nobody has written yet\n"
            "requires:\n  actions: [write_util]\n---\n"
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


def test_memory_and_script_kinds_are_base_kinds():
    """Every routine held the notebook and its own scripts; none could work without
    them, so they are no longer switches: an empty policy allows all three kinds."""
    none = GrantPolicy()
    assert none.deny({"kind": "memory_write", "name": "x"}) is None
    assert none.deny({"kind": "memory_read", "name": "x"}) is None
    assert none.allows_kind("script")
    assert not none.allows_kind("shell")               # …while a real switch stays off


def test_admin_lifts_capability_gating_only(tmp_path):
    """D62: an admin conversation leg lifts CAPABILITY gating (gated kinds, reserved utils,
    previous-run read depth) but leaves every STRUCTURAL / ownership gate in force."""
    # A stock (no-capability) policy denies gated kinds + reserved utils; its admin twin allows.
    lib = _lib(tmp_path, {"util-authoring": AUTHORING, "messaging-discord": COMMUNICATION})
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
    assert r and "recipe-authoring" in r
    #  - routine.yaml config is the user's, denied for everyone including admin
    c = admin.deny({"kind": "write_file", "path": "routine.yaml", "content": "x"})
    assert c is not None


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
        '"""does a thing.\n\ntags: a, b, c\nsecrets: (none)\ncalls: (none)\nnet: none\nfs: roots\n'
        'usage: gu existing\n"""\n', encoding="utf-8")
    creator = load_policy(home, ["util-authoring"], {"actions": ["write_util"]})
    reviser = load_policy(home, ["util-revision"], {"actions": ["revise_util"]})
    denial = creator.deny(_write_util("existing"))
    assert denial and "REVISION" in denial and "util-revision" in denial
    assert reviser.deny(_write_util("existing")) is None
    # …and the create-only holder can still create
    assert creator.deny(_write_util("not-there-yet")) is None


def test_a_half_granted_for_the_run_still_tells_create_from_revise(tmp_path):
    """The split used to read a catalog loaded WITH the policy, and only for a routine holding
    exactly one half. A routine holding neither loaded nothing: an existing util read as a
    CREATION (so the denial asked for `action:write_util`), and once the user granted that half
    for the run — `with_overlay` keeps the base policy's catalog — write_util REVISED the
    existing util under a creation grant. The handler checks nothing after `deny` but the
    approval dial, so with `confirm: creations` nobody was asked."""
    home = _lib(tmp_path, {"util-authoring": AUTHORING, "util-revision": REVISION})
    (home.parent / "utils" / "existing").mkdir(parents=True)
    (home.parent / "utils" / "existing" / "main.py").write_text('"""does a thing."""\n',
                                                                encoding="utf-8")
    neither = load_policy(home, [], {"confirm": "creations"})
    denial = neither.deny(_write_util("existing"))
    assert denial and "REVISION" in denial and '"action:revise_util"' in denial
    creation_for_the_run = neither.with_overlay({"action:write_util"}, set())
    refused = creation_for_the_run.deny(_write_util("existing"))
    assert refused and "REVISION" in refused, "a creation grant must not revise"
    assert creation_for_the_run.deny(_write_util("brand-new")) is None
    # a util created mid-run is a revision the next time it is written
    (home.parent / "utils" / "brand-new").mkdir()
    (home.parent / "utils" / "brand-new" / "main.py").write_text('"""new."""\n',
                                                                 encoding="utf-8")
    assert creation_for_the_run.deny(_write_util("brand-new"))


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
    # a doc that reserves ONLY `signal:send` gates that verb and nothing else: reading a
    # channel needs its credential, sending needs the permission
    verb_only = _lib(tmp_path / "v", {"send": SIGNAL_DOC.replace("utils: [signal]",
                                                                "utils: [signal:send]")})
    pol = load_policy(verb_only, [], {})
    assert "signal" not in pol.gated_utils and pol.gated_verbs == {"signal": {"send": ("send",)}}
    denial = pol.deny({"kind": "util", "name": "signal", "args": ["send", "hi"]})
    assert denial and "signal send" in denial and "send" in denial
    assert pol.deny({"kind": "util", "name": "signal", "args": ["read"]}) is None
    # the verb grant opens exactly the reserved verb
    granted = load_policy(verb_only, ["send"], {"utils": ["signal:send"]})
    assert granted.deny({"kind": "util", "name": "signal", "args": ["send", "hi"]}) is None


# --- expects: the SOFT dependency edge ---------------------------------------------------

def test_normalize_expects_validates_class_and_name():
    """`expects:` is entity CLASS → names, with '*' for 'at least one'. A bad row is dropped
    and reported, never raised — a soft edge must not be able to break a run."""
    from rsched.grants import normalize_expects

    out, problems = normalize_expects({"machine": ["*"], "secret": ["STATUS_HOST_TOKEN"],
                                       "fs-write": "/srv/site"})
    assert out == {"machine": ["*"], "secret": ["STATUS_HOST_TOKEN"],
                   "fs-write": ["/srv/site"]}
    assert problems == []
    out, problems = normalize_expects({"nonsense": ["x"]})
    assert out == {} and any("unknown entity class" in p for p in problems)
    out, problems = normalize_expects({"secret": ["not a secret name"]})
    assert out == {} and any("not a valid secret entity name" in p for p in problems)
    assert normalize_expects(None) == ({}, [])
    assert normalize_expects(["not", "a", "mapping"])[1]


def test_read_library_expects_reads_permissions_and_rules(tmp_path):
    """One reader for both halves: a permission may require AND expect, a rule may only
    expect. Docs declaring neither are simply absent from the map."""
    from rsched.grants import read_library_expects

    (tmp_path / "permissions").mkdir()
    (tmp_path / "rules").mkdir()
    (tmp_path / "permissions" / "remote-machines.md").write_text(
        "---\ntags: [a]\nrequires:\n  utils: [remote]\nexpects:\n  machine: ['*']\n---\n"
        "# permission: remote-machines — x\n", encoding="utf-8")
    (tmp_path / "permissions" / "plain.md").write_text(
        "---\ntags: [a]\nrequires: {}\n---\n# permission: plain — x\n", encoding="utf-8")
    (tmp_path / "rules" / "status-page.md").write_text(
        "---\ntags: [a, b, c]\nexpects:\n  fs-write: ['*']\n---\n# rule: status page — x\n",
        encoding="utf-8")

    assert read_library_expects(tmp_path / "permissions") == {
        "remote-machines": {"machine": ["*"]}}
    assert read_library_expects(tmp_path / "rules") == {"status-page": {"fs-write": ["*"]}}
    assert read_library_expects(tmp_path / "nope") == {}
