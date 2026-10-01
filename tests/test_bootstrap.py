"""One-time adoption of new default permissions (bootstrap.py)."""

import json

import yaml

from rsched import bootstrap
from rsched.bootstrap import _ADOPTED_MARKER, adopt_permissions

PERM = ("---\ntags: [a, b, c]\nrequires:\n  actions: [schedule_run]\n---\n"
        "# permission: scheduling — test one-shots\nbody\n")


def _mk_library(tmp_path):
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)
    (perms / "scheduling.md").write_text(PERM, encoding="utf-8")
    return perms


def _set_permissions(routine_dir, slugs):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["permissions"] = slugs
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_adopt_appends_slug_once(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    d = make_routine(slug="r1")
    _set_permissions(d, ["util-authoring"])
    perms = _mk_library(tmp_path)
    home = tmp_path / "routines"

    assert adopt_permissions(home, perms) == 1
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["permissions"] == ["util-authoring", "scheduling"]
    assert json.loads((home / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["scheduling"]

    # The user revokes it later: adoption is marker-gated, so the next boot must NOT re-add it.
    _set_permissions(d, ["util-authoring"])
    assert adopt_permissions(home, perms) == 0
    assert yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"] \
        == ["util-authoring"]


def test_adopt_leaves_implicit_default_lists_alone(make_routine, tmp_path, monkeypatch):
    # No `permissions:` key = the routine follows DEFAULT_PERMISSIONS (which now includes
    # the slug). Writing an explicit list would SHRINK its held set.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    d = make_routine(slug="r2")
    perms = _mk_library(tmp_path)
    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "permissions" not in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))


def test_adopt_skips_dot_dirs_and_already_active(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    d = make_routine(slug="r3")
    _set_permissions(d, ["scheduling"])
    hidden = tmp_path / "routines" / ".scratch-20260712-000000"
    hidden.mkdir(parents=True)
    (hidden / "routine.yaml").write_text("permissions: [util-authoring]\n", encoding="utf-8")
    perms = _mk_library(tmp_path)

    assert adopt_permissions(tmp_path / "routines", perms) == 0
    assert "scheduling" not in (hidden / "routine.yaml").read_text(encoding="utf-8")
    # already-adopted slugs are still marked done so the next boot skips the scan
    assert json.loads((tmp_path / "routines" / _ADOPTED_MARKER).read_text(encoding="utf-8")) == ["scheduling"]


def test_adopt_waits_for_a_library(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    make_routine(slug="r4")
    missing = tmp_path / "libraries" / "permissions"     # never created → no library yet
    assert adopt_permissions(tmp_path / "routines", missing) == 0
    assert not (tmp_path / "routines" / _ADOPTED_MARKER).exists()   # retried next boot


def test_adopt_seeds_missing_library_copy_from_repo_seed(make_routine, tmp_path, monkeypatch):
    # An existing library repo predates the permission: the repo seed is copied in (never
    # overwriting) so the library copy exists as the grants authority.
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    d = make_routine(slug="r5")
    _set_permissions(d, [])
    perms = tmp_path / "libraries" / "permissions"
    perms.mkdir(parents=True)

    assert adopt_permissions(tmp_path / "routines", perms) == 1
    assert (perms / "scheduling.md").exists()
    assert "scheduling" in yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["permissions"]


# ------------------------------------------------------------------ seed syncing


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
    assert bootstrap.sync_seed_utils(lib, routines_home=tmp_path) == 1
    assert (lib / "utils" / "newutil" / "main.py").read_text(encoding="utf-8") == "# seed newutil\n"
    assert (lib / "utils" / "oldutil" / "main.py").read_text(encoding="utf-8") == "# locally modified\n"
    # second boot: nothing new, nothing touched
    assert bootstrap.sync_seed_utils(lib, routines_home=tmp_path) == 0


def test_sync_seed_utils_no_library_yet(tmp_path, monkeypatch):
    """Before seed_libraries has created utils/, the sync is a silent no-op."""
    from rsched import bootstrap
    fake_repo = tmp_path / "repo"
    (fake_repo / "util-seed" / "utils" / "x").mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    assert bootstrap.sync_seed_utils(tmp_path / "nolib", routines_home=tmp_path) == 0


def test_adopt_library_edits_commits_out_of_band_writes(tmp_path):
    """R332/R335: files written into the live library by a conversation's fs grant (or
    the user's editor) have no committing writer — boot adopts them so they get history.
    """
    from rsched import libgit
    from rsched.bootstrap import adopt_library_edits

    home = tmp_path / "libraries"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "seeded.md").write_text("# rule: seeded — x\n", encoding="utf-8")
    libgit.init_repo(home, first_commit="seed library repo")
    assert adopt_library_edits(home, routines_home=tmp_path) is False           # clean repo → nothing to adopt
    (home / "rules" / "loose.md").write_text("# rule: loose — y\n", encoding="utf-8")
    (home / "rules" / "seeded.md").write_text("# rule: seeded — edited\n", encoding="utf-8")
    assert adopt_library_edits(home, routines_home=tmp_path) is True            # untracked + modified both adopted
    assert libgit.git(home, "status", "--porcelain").stdout.strip() == ""
    assert adopt_library_edits(home, routines_home=tmp_path) is False           # idempotent on the next boot
    assert adopt_library_edits(tmp_path / "nogit", routines_home=tmp_path) is False   # no repo → no-op


def test_adopt_raises_the_actions_the_doc_requires_and_keeps_the_settings(make_routine,
                                                                          tmp_path,
                                                                          monkeypatch):
    """The adopt cascade is `grants.capabilities_for` + the floor, never a private copy: the
    doc's actions are raised; the settings the person chose per routine stay as they are
    (a doc can require only actions and utils — a setting is never switched on by one)."""
    perms = _mk_library(tmp_path)
    monkeypatch.setattr(bootstrap, "ADOPT_PERMISSIONS", ["scheduling"])
    d = make_routine(slug="r1")
    _set_permissions(d, [])
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["capabilities"] = {"actions": [], "utils": [], "confirm": "always", "runs": "all",
                           "reminders": "none"}
    (d / "routine.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert adopt_permissions(tmp_path / "routines", perms) == 1
    caps = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))["capabilities"]
    assert caps["actions"] == ["schedule_run"]
    assert (caps["runs"], caps["reminders"], caps["confirm"]) == ("all", "none", "always")


def test_the_implicit_default_block_carries_the_reminders_setting():
    """A routine with no `capabilities:` key means DEFAULT_CAPABILITIES; adopt deliberately
    does not write a block for one — so the default itself must carry the one setting that is
    on by default and the floor must keep it with no permission behind it."""
    from rsched.config.base import DEFAULT_CAPABILITIES
    from rsched.grants import floor_capabilities

    assert DEFAULT_CAPABILITIES["reminders"] == "local"
    assert floor_capabilities([], {}, DEFAULT_CAPABILITIES)["reminders"] == "local"
