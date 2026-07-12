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
    # new-world seeds arrive alongside (run-history, self-modification, shell, …)
    assert (lib / "permissions" / "self-modification.md").exists()
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
    # permission slugs kept, self-modification added (the behavior routines always had)
    assert raw["permissions"] == ["util-authoring", "communication", "self-modification"]
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
