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


def test_a_fresh_containers_first_boot_seeds_utils_and_docs(tmp_path, monkeypatch):
    """A container has no install step: its library is an EMPTY bind mount at first boot. The
    seed util sync only installs into an existing utils/, and the repo used to be created by
    the web lifespan — after the syncs had run — so a new deploy started with docs but no
    utils, and first got them at its second boot. The daemon's boot creates the repo first."""
    from types import SimpleNamespace

    from rsched import cli_daemon

    lib = tmp_path / "lib"
    lib.mkdir()                                            # what a fresh bind mount leaves
    server = SimpleNamespace(libraries_home=lib, libraries_remote="", bind="127.0.0.1",
                             port=8321, routines_home=tmp_path / "routines",
                             permissions_home=lib / "permissions")
    server.routines_home.mkdir()
    monkeypatch.setattr(bootstrap, "ensure_config", lambda: False)
    monkeypatch.setattr(cli_daemon, "load_server_config", lambda: (server, []))
    monkeypatch.setattr("rsched.migrate_settings_patterns.run_migration", lambda s: {})
    monkeypatch.setattr("rsched.web.app.create_app", lambda s: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    assert cli_daemon.cmd_daemon(None) == 0
    assert (lib / ".git").is_dir()
    assert (lib / "utils" / "remote" / "main.py").is_file()       # utils at the FIRST boot
    assert list((lib / "rules").glob("*.md"))
    from rsched import libgit
    assert libgit.git(lib, "status", "--porcelain").stdout.strip() == ""   # all committed


def test_sync_seed_utils_no_library_yet(tmp_path, monkeypatch):
    """Before the library exists (the daemon's boot creates it first), the sync is a silent
    no-op."""
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


# ------------------------------------------------------------------ the config's tokens


def _config_at(tmp_path, monkeypatch, text: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setattr(bootstrap, "config_file", lambda: cfg)
    return cfg


def test_an_install_sh_config_never_keeps_the_examples_routine_token(tmp_path, monkeypatch):
    """deploy/install.sh copies the example and generates only the PRIMARY token, so the
    routine tier's bearer was the example's `change-me-too` — a value published in the repo,
    which opens every GET outside the denied subtrees to anyone who read it. A known
    placeholder is no secret: boot replaces it, and the primary is left exactly as it was."""
    example = (bootstrap.repo_root() / "config" / "config.example.yaml").read_text(
        encoding="utf-8")
    cfg = _config_at(tmp_path, monkeypatch,
                     example.replace('token: "change-me"', 'token: "generated-primary"', 1))
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["routine_token"] == "change-me-too"

    assert bootstrap.ensure_config() is False
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["token"] == "generated-primary"
    assert raw["routine_token"] not in ("change-me-too", "change-me", "generated-primary")
    assert len(raw["routine_token"]) >= 24


def test_a_routine_token_equal_to_the_primary_is_replaced(tmp_path, monkeypatch):
    """R94: the primary must never double as the routine tier, or the seal is vacuous."""
    cfg = _config_at(tmp_path, monkeypatch, 'token: "same"\nroutine_token: "same"\n')
    assert bootstrap.ensure_config() is False
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["token"] == "same" and raw["routine_token"] not in ("same", "")


def test_a_real_or_deliberately_empty_routine_token_is_left_alone(tmp_path, monkeypatch):
    """Its own secret stays byte-identical; an explicit empty value is the documented way to
    switch the tier off (config/server.py), so boot never 'repairs' it either."""
    for text in ('token: "p"\nroutine_token: "a-real-secret"  # mine\n',
                 'token: "p"\nroutine_token: ""\n'):
        cfg = _config_at(tmp_path, monkeypatch, text)
        assert bootstrap.ensure_config() is False
        assert cfg.read_text(encoding="utf-8") == text


def test_the_implicit_default_block_carries_the_reminders_setting():
    """A routine with no `capabilities:` key means DEFAULT_CAPABILITIES; adopt deliberately
    does not write a block for one — so the default itself must carry the one setting that is
    on by default and the floor must keep it with no permission behind it."""
    from rsched.config.base import DEFAULT_CAPABILITIES
    from rsched.grants import floor_capabilities

    assert DEFAULT_CAPABILITIES["reminders"] == "local"
    assert floor_capabilities([], {}, DEFAULT_CAPABILITIES)["reminders"] == "local"
