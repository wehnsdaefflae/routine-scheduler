"""One-time adoption of new default permissions + the fragments→traits/permissions
migration (bootstrap.py)."""

import json

import yaml

import rsched.bootstrap as bootstrap
from rsched.bootstrap import _ADOPTED_MARKER, adopt_permissions, migrate_fragments_split

PERM = ("---\ntags: [a, b, c]\ngrants:\n  actions: [memory_read, memory_write]\n---\n"
        "# permission: memory — test notes\nbody\n")


def _mk_library(tmp_path):
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)
    (perms / "memory.md").write_text(PERM, encoding="utf-8")
    return perms


def _set_permissions(routine_dir, slugs):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["permissions"] = slugs
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_adopt_appends_slug_once(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r1")
    _set_permissions(d, ["util-authoring"])
    perms = _mk_library(tmp_path)
    home = tmp_path / "routines"

    assert adopt_permissions(home, perms) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["permissions"] == ["util-authoring", "memory"]
    assert json.loads((home / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]

    # The user revokes it later: adoption is marker-gated, so the next boot must NOT re-add it.
    _set_permissions(d, ["util-authoring"])
    assert adopt_permissions(home, perms) == 0
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"] \
        == ["util-authoring"]


def test_adopt_leaves_implicit_default_lists_alone(make_routine, tmp_path, monkeypatch):
    # No `permissions:` key = the routine follows DEFAULT_PERMISSIONS (which now includes
    # the slug). Writing an explicit list would SHRINK its held set.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r2")
    perms = _mk_library(tmp_path)
    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "permissions" not in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))


def test_adopt_skips_dot_dirs_and_already_active(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r3")
    _set_permissions(d, ["memory"])
    wizard = tmp_path / "routines" / ".wizard-20260712-000000"
    wizard.mkdir(parents=True)
    (wizard / "routine.yaml").write_text("permissions: [util-authoring]\n", encoding="utf-8")
    perms = _mk_library(tmp_path)

    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "memory" not in (wizard / "routine.yaml").read_text(encoding="utf-8")
    # already-adopted slugs are still marked done so the next boot skips the scan
    assert json.loads((tmp_path / "routines" / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["memory"]


def test_adopt_waits_for_a_library(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    make_routine(slug="r4")
    missing = tmp_path / "libraries" / "permissions"     # never created → no library yet
    assert adopt_permissions(tmp_path / "routines", missing) == 0
    assert not (tmp_path / "routines" / _ADOPTED_MARKER).exists()   # retried next boot


def test_adopt_seeds_missing_library_copy_from_repo_seed(make_routine, tmp_path, monkeypatch):
    # An existing library repo predates the permission: the repo seed is copied in (never
    # overwriting) so the library copy exists as the grants authority.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["memory"])
    d = make_routine(slug="r5")
    _set_permissions(d, [])
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)

    assert adopt_permissions(tmp_path / "routines", perms) == 1
    assert (perms / "memory.md").exists()
    assert "memory" in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"]


# ------------------------------------------------------------------ the 2026-07 split


OLD_PROSE = "---\ntags: [a, b, c]\n---\n# fragment: ask policy — when to ask\nbody\n"
OLD_GRANTING = ("---\ntags: [a, b, c]\ngrants:\n  utils: [zulip]\n---\n"
                "# fragment: zulip-channel — a custom channel\nbody\n")


def _old_library(tmp_path):
    lib = tmp_path / "libraries"
    frags = lib / "fragments"
    frags.mkdir(parents=True)
    (frags / "ask-policy.md").write_text(OLD_PROSE, encoding="utf-8")
    (frags / "communication.md").write_text(OLD_GRANTING, encoding="utf-8")
    (frags / "zulip-channel.md").write_text(OLD_GRANTING, encoding="utf-8")
    return lib


def test_migrate_splits_the_library(tmp_path):
    lib = _old_library(tmp_path)
    migrate_fragments_split(tmp_path / "routines", lib)
    assert not (lib / "fragments").exists()
    # known slugs come from the current repo seeds; unknown ones move by grants-presence
    assert (lib / "traits" / "ask-policy.md").exists()
    assert (lib / "permissions" / "communication.md").exists()
    zulip = (lib / "permissions" / "zulip-channel.md").read_text(encoding="utf-8")
    assert "# permission: zulip-channel" in zulip
    # new-world seeds arrive alongside (run-history, shell, …)
    assert (lib / "permissions" / "run-history.md").exists()
    # idempotent: a second call finds no fragments/ dir and changes nothing
    migrate_fragments_split(tmp_path / "routines", lib)


def test_migrate_converts_a_routine(make_routine, tmp_path):
    lib = _old_library(tmp_path)
    d = make_routine(slug="legacy")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["fragments"] = ["ask-policy", "util-authoring", "communication"]
    (d / "routine.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    (d / "fragments").mkdir()
    (d / "fragments" / "ask-policy.md").write_text(OLD_PROSE, encoding="utf-8")
    (d / "fragments" / "communication.md").write_text(OLD_GRANTING, encoding="utf-8")

    assert migrate_fragments_split(tmp_path / "routines", lib) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert "fragments" not in raw
    # permission slugs kept (self-modification is retired — never granted anymore)
    assert raw["permissions"] == ["util-authoring", "communication"]
    assert not (d / "fragments").exists()
    trait = (d / "traits" / "ask-policy.md").read_text(encoding="utf-8")
    assert "# trait: ask policy" in trait
    assert not (d / "traits" / "communication.md").exists()   # permission prose lives in the library
    main = (d / "main.md").read_text(encoding="utf-8")
    assert "## Standing practices" in main and "traits/ask-policy.md" in main


def test_migrate_maps_implicit_default_to_default_permissions(make_routine, tmp_path):
    from rsched.config import DEFAULT_PERMISSIONS

    lib = _old_library(tmp_path)
    d = make_routine(slug="implicit")
    (d / "fragments").mkdir()
    (d / "fragments" / "ask-policy.md").write_text(OLD_PROSE, encoding="utf-8")

    assert migrate_fragments_split(tmp_path / "routines", lib) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["permissions"] == list(DEFAULT_PERMISSIONS)


def test_sync_seed_utils_installs_missing_never_overwrites(tmp_path, monkeypatch):
    """A util added to util-seed after bootstrap reaches the live catalog at daemon boot;
    an existing (possibly locally-modified) util is never touched."""
    from rsched import bootstrap
    fake_repo = tmp_path / "repo"
    for name in ("newutil", "oldutil"):
        (fake_repo / "util-seed" / "utils" / name).mkdir(parents=True)
        (fake_repo / "util-seed" / "utils" / name / "main.py").write_text(
            f"# seed {name}\n", encoding="utf-8")
    lib = tmp_path / "lib"
    (lib / "utils" / "oldutil").mkdir(parents=True)
    (lib / "utils" / "oldutil" / "main.py").write_text("# locally modified\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    assert bootstrap.sync_seed_utils(lib) == 1
    assert (lib / "utils" / "newutil" / "main.py").read_text(encoding="utf-8") == "# seed newutil\n"
    assert (lib / "utils" / "oldutil" / "main.py").read_text(encoding="utf-8") == "# locally modified\n"
    # second boot: nothing new, nothing touched
    assert bootstrap.sync_seed_utils(lib) == 0


def test_sync_seed_utils_no_library_yet(tmp_path, monkeypatch):
    """Before seed_libraries has created utils/, the sync is a silent no-op."""
    from rsched import bootstrap
    fake_repo = tmp_path / "repo"
    (fake_repo / "util-seed" / "utils" / "x").mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    assert bootstrap.sync_seed_utils(tmp_path / "nolib") == 0


def test_migrate_improvement_split(tmp_path):
    """The 2026-07 consolidation: library-sync retires to .archive/, meta-workflows becomes
    workflow-curator, routine-improver installs, improve-* traits vanish from the library
    and from every routine's own files (main.md references included)."""
    from rsched.bootstrap import migrate_improvement_split

    home = tmp_path / "routines"
    lib = tmp_path / "library"
    (lib / "traits").mkdir(parents=True)
    (lib / "traits" / "improve-bugfix.md").write_text("# trait: improve-bugfix — x")
    (lib / "traits" / "ask-policy.md").write_text("# trait: ask policy — x")

    ls = home / "library-sync"
    ls.mkdir(parents=True)
    (ls / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "library-sync", "workflow": {"library_slug": "library-sync"}}))

    mw = home / "meta-workflows"
    (mw / "traits").mkdir(parents=True)
    (mw / "traits" / "improve-ui.md").write_text("# trait: improve-ui — x")
    (mw / "traits" / "ask-policy.md").write_text("# trait: ask policy — x")
    (mw / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "meta-workflows", "name": "Meta: workflow library", "description": "d"}))
    (mw / "main.md").write_text(
        "---\nname: 'Meta: workflow library'\nslug: meta-workflows\nincludes:\n"
        "- ask-policy\n- improve-ui\n---\n\nbody\n\n## Standing practices\n\n"
        "- `traits/ask-policy.md` — ask\n- `traits/improve-ui.md` — polish\n\n"
        "After the main work, before finish, run each improve pass in its own module: "
        "`traits/improve-ui.md`.\n")

    touched = migrate_improvement_split(home, lib)
    assert touched >= 1
    assert not (home / "library-sync").exists()
    assert (home / ".archive" / "library-sync-retired" / "routine.yaml").is_file()
    wc = home / "workflow-curator"
    assert wc.is_dir() and not (home / "meta-workflows").exists()
    raw = yaml.safe_load((wc / "routine.yaml").read_text())
    assert raw["slug"] == "workflow-curator" and raw["name"] == "Workflow curator"
    assert (home / "routine-improver" / "main.md").exists()          # installed on existing instances
    assert (home / "routine-improver" / "steps" / "fresh-eyes.md").exists()
    assert not (lib / "traits" / "improve-bugfix.md").exists()
    assert (lib / "traits" / "ask-policy.md").exists()
    assert not list((wc / "traits").glob("improve-*.md"))
    main = (wc / "main.md").read_text()
    assert "improve-" not in main and "traits/ask-policy.md" in main
    assert migrate_improvement_split(home, lib) == 0                 # idempotent


def test_migrate_repoints_self_audit_off_fragment_gate(tmp_path):
    from rsched.bootstrap import _SELF_AUDIT_LEGACY, migrate_improvement_split

    home = tmp_path / "routines"
    sa = home / "self-audit"
    (sa / "steps").mkdir(parents=True)
    (sa / "routine.yaml").write_text(yaml.safe_dump({"slug": "self-audit", "description": "d"}))
    (sa / "instruction.md").write_text("# Self-audit\n\n" + _SELF_AUDIT_LEGACY + "\n\ntail\n")
    (sa / "steps" / "act-apply-fixes.md").write_text(
        "APPLY may only contain what the autonomy gate authorized: items covered by an ACTIVE\n"
        "`improve-*` fragment, plus decisions the user settled. **If no `improve-*` fragment is active\n"
        "and no settled decision is pending, APPLY must be empty** — this is a report-only run.\n"
        "If APPLY is empty, skip straight to Next (a no-change run is a good run — say so in the report).\n")
    migrate_improvement_split(home, tmp_path / "library")
    text = (sa / "instruction.md").read_text()
    assert "fragment toggles" not in text and "routine-improver" in text
    step = (sa / "steps" / "act-apply-fixes.md").read_text()
    assert "fragment" not in step and "items inside your lenses" in step


def test_adopt_unlimited_tokens_once(tmp_path):
    """Every existing routine/conversation with a pinned finite token budget flips to -1 —
    exactly once (marker): a cap the user re-pins afterwards stays."""
    from rsched.bootstrap import adopt_unlimited_tokens

    routines = tmp_path / "routines"
    convs = tmp_path / "conversations"
    for home, slug, tokens in ((routines, "r1", 1_500_000), (convs, "c-1", 400_000)):
        d = home / slug
        d.mkdir(parents=True)
        (d / "routine.yaml").write_text(yaml.safe_dump(
            {"slug": slug, "budgets": {"max_turns": 10, "max_total_tokens": tokens}}))
    (routines / "r2").mkdir()
    (routines / "r2" / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "r2", "budgets": {"max_turns": 10}}))     # implicit → follows the default

    assert adopt_unlimited_tokens(routines, convs) == 2
    for home, slug in ((routines, "r1"), (convs, "c-1")):
        raw = yaml.safe_load((home / slug / "routine.yaml").read_text())
        assert raw["budgets"]["max_total_tokens"] == -1
    raw = yaml.safe_load((routines / "r2" / "routine.yaml").read_text())
    assert "max_total_tokens" not in raw["budgets"]        # untouched — default applies

    # user pins a finite cap later → the one-time adoption never overrides it again
    raw = yaml.safe_load((routines / "r1" / "routine.yaml").read_text())
    raw["budgets"]["max_total_tokens"] = 250_000
    (routines / "r1" / "routine.yaml").write_text(yaml.safe_dump(raw))
    assert adopt_unlimited_tokens(routines, convs) == 0
    raw = yaml.safe_load((routines / "r1" / "routine.yaml").read_text())
    assert raw["budgets"]["max_total_tokens"] == 250_000


def test_migrate_strips_improve_includes_from_library_workflows(tmp_path):
    """A live library workflow still including retired improve-* traits would lint red
    forever (seed-sync never overwrites) — the migration strips the entries in place,
    scoped to the includes list (prose mentioning a lens stays)."""
    from rsched.bootstrap import migrate_improvement_split

    home = tmp_path / "routines"
    home.mkdir()
    lib = tmp_path / "library"
    (lib / "workflows").mkdir(parents=True)
    wf = lib / "workflows" / "demo-flow.py"
    wf.write_text(
        'META = {"slug": "demo-flow", "includes": ["ask-policy", "improve-ui",\n'
        '                                          "improve-bugfix"]}\n'
        'DOC = "the improve-ui lens used to live here"\n')
    migrate_improvement_split(home, lib)
    text = wf.read_text()
    assert '"improve-ui"' not in text.split("DOC")[0]
    assert '"ask-policy"' in text
    assert "the improve-ui lens used to live here" in text     # prose untouched


def test_retire_self_modification_everywhere(tmp_path):
    """The permission no longer exists: the library doc is deleted and the slug leaves
    every routine/conversation yaml — the improver included (its unlock is fs_write_roots).
    Idempotent: safe to run every boot."""
    from rsched.bootstrap import retire_self_modification

    routines = tmp_path / "routines"
    convs = tmp_path / "conversations"
    perms_home = tmp_path / "library" / "permissions"
    perms_home.mkdir(parents=True)
    (perms_home / "self-modification.md").write_text("# permission: self-modification — x")
    (perms_home / "memory.md").write_text("# permission: memory — x")
    for home, slug in ((routines, "worker"), (routines, "routine-improver"), (convs, "c-1")):
        d = home / slug
        d.mkdir(parents=True)
        (d / "routine.yaml").write_text(yaml.safe_dump(
            {"slug": slug, "permissions": ["util-authoring", "memory", "self-modification"]}))
    (routines / ".self-modification-revoked").write_text("done\n")   # pre-retirement marker

    assert retire_self_modification(routines, convs, perms_home) == 3
    assert not (perms_home / "self-modification.md").exists()
    assert (perms_home / "memory.md").exists()
    assert not (routines / ".self-modification-revoked").exists()
    for home, slug in ((routines, "worker"), (routines, "routine-improver"), (convs, "c-1")):
        perms = yaml.safe_load((home / slug / "routine.yaml").read_text())["permissions"]
        assert "self-modification" not in perms, (slug, perms)
        assert "memory" in perms
    assert retire_self_modification(routines, convs, perms_home) == 0   # idempotent


def test_adopt_seed_routine_installs_once_and_respects_archive(tmp_path):
    """A seed added after first boot lands ONCE on an existing instance; an installed or
    archived copy is never clobbered (archived = the user removed it on purpose)."""
    from rsched.bootstrap import adopt_seed_routine

    routines = tmp_path / "routines"
    (routines / "worker").mkdir(parents=True)          # existing instance, not fresh
    assert adopt_seed_routine(routines, "token-lab") is True
    assert (routines / "token-lab" / "routine.yaml").is_file()
    assert (routines / "token-lab" / "artifacts").exists() is False   # seed ships no artifacts
    assert adopt_seed_routine(routines, "token-lab") is False         # idempotent

    # archived copy → respected, never re-installed
    import shutil
    archive = routines / ".archive"
    archive.mkdir()
    shutil.move(str(routines / "token-lab"), str(archive / "token-lab"))
    assert adopt_seed_routine(routines, "token-lab") is False
    assert not (routines / "token-lab").exists()

    # unknown seed slug → no-op
    assert adopt_seed_routine(routines, "no-such-seed") is False


def test_migrate_capability_split(tmp_path):
    """The two-layer split: library docs lose `grants:` (retired variants deleted, seed
    docs replaced, user docs mechanically renamed to requires: minus confirm) and every
    yaml with an explicit permissions list gains the equivalent capabilities mapping,
    collapsed to the surviving doc slugs. Idempotent: a second run touches nothing."""
    from rsched.bootstrap import migrate_capability_split

    routines = tmp_path / "routines"
    convs = tmp_path / "conversations"
    perms_home = tmp_path / "library" / "permissions"
    perms_home.mkdir(parents=True)
    (perms_home / "util-authoring.md").write_text(
        "---\ngrants:\n  actions: [write_util]\n  confirm: true\n---\n"
        "# permission: util authoring — old seed\nbody\n")
    (perms_home / "util-authoring-full-auto.md").write_text(
        "---\ngrants:\n  actions: [write_util]\n  confirm: false\n---\n"
        "# permission: util authoring (full auto) — old variant\nbody\n")
    (perms_home / "run-history-full.md").write_text(
        "---\ngrants:\n  runs: all\n---\n# permission: run history (full) — old\nbody\n")
    (perms_home / "zulip-channel.md").write_text(
        "---\ntags: [a, b, c]\ngrants:\n  utils: [zulip]\n  confirm: true\n---\n"
        "# permission: zulip-channel — a user-authored doc\nbody\nmore\n")

    worker = routines / "worker"
    worker.mkdir(parents=True)
    (worker / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "worker",
         "permissions": ["util-authoring-full-auto", "run-history-full", "zulip-channel"]}))
    silent = routines / "silent"          # no explicit list → follows the defaults, untouched
    silent.mkdir(parents=True)
    (silent / "routine.yaml").write_text(yaml.safe_dump({"slug": "silent"}))
    conv = convs / "c-1"
    conv.mkdir(parents=True)
    (conv / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "c-1", "kind": "conversation", "permissions": ["communication"]}))
    (perms_home / "communication.md").write_text(
        "---\ngrants:\n  utils: [discord]\n---\n# permission: communication — old seed\nbody\n")

    assert migrate_capability_split(routines, convs, perms_home) == 2

    # library: retired variants gone, seed docs now carry requires:, user doc renamed
    assert not (perms_home / "util-authoring-full-auto.md").exists()
    assert not (perms_home / "run-history-full.md").exists()
    ua = (perms_home / "util-authoring.md").read_text()
    assert "requires:" in ua and "grants:" not in ua
    zulip = (perms_home / "zulip-channel.md").read_text()
    assert "requires:" in zulip and "grants:" not in zulip and "confirm" not in zulip

    raw = yaml.safe_load((worker / "routine.yaml").read_text())
    assert raw["permissions"] == ["util-authoring", "run-history", "zulip-channel"]
    caps = raw["capabilities"]
    assert caps["actions"] == ["write_util"] and caps["confirm"] == "never"
    assert caps["runs"] == "all" and caps["utils"] == ["zulip"]

    craw = yaml.safe_load((conv / "routine.yaml").read_text())
    assert craw["capabilities"]["utils"] == ["discord"]
    assert "capabilities" not in yaml.safe_load((silent / "routine.yaml").read_text())

    assert migrate_capability_split(routines, convs, perms_home) == 0   # idempotent
