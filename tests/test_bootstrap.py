"""Boot bootstrap (bootstrap.py): the seed syncs, out-of-band library edits, and the
config tokens a fresh or hand-copied install must never keep."""

import pytest
import yaml

from rsched import bootstrap

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


@pytest.mark.parametrize("bind_mounted", [True, False])
def test_a_fresh_containers_first_boot_seeds_utils_and_docs(tmp_path, monkeypatch,
                                                            bind_mounted):
    """A container has no install step. Its library is an EMPTY bind mount at first boot — or
    no directory at all (a fresh HOME). The seed syncs only install into a library that
    exists, and the repo used to be created by the web lifespan AFTER they had run: the first
    boot started without utils (and, with no directory, without `converse`, rules or
    patterns) until the next restart. The daemon's boot creates the repo first."""
    from types import SimpleNamespace

    from rsched import cli_daemon

    lib = tmp_path / "lib"
    if bind_mounted:
        lib.mkdir()                                        # what a fresh bind mount leaves
    server = SimpleNamespace(libraries_home=lib, libraries_remote="", bind="127.0.0.1",
                             port=8321, routines_home=tmp_path / "routines",
                             permissions_home=lib / "permissions")
    server.routines_home.mkdir()
    monkeypatch.setattr(bootstrap, "ensure_config", lambda: False)
    monkeypatch.setattr(cli_daemon, "load_server_config", lambda: (server, []))
    monkeypatch.setattr("rsched.migrate_settings_patterns.run_migration", lambda s: {})
    monkeypatch.setattr("rsched.migrate_enabled.run_migration", lambda s: {})
    monkeypatch.setattr("rsched.web.app.create_app", lambda s: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    assert cli_daemon.cmd_daemon(None) == 0
    assert (lib / ".git").is_dir()
    assert (lib / "utils" / "remote" / "main.py").is_file()       # utils at the FIRST boot
    assert (lib / "workflows" / "converse.py").is_file()          # conversations can start
    assert list((lib / "rules").glob("*.md")) and list((lib / "patterns").glob("*.yaml"))
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


def test_a_placeholder_or_missing_primary_token_is_replaced(tmp_path, monkeypatch):
    """A config hand-copied from the example carries `token: "change-me"` — the console's
    full-authority bearer, published in the repo — and one with no `token:` line loads as an
    empty token, i.e. auth off. Both get a fresh primary; the routine token is kept."""
    for text in ('token: "change-me"\nroutine_token: "own-secret"\n',
                 'bind: 127.0.0.1\nroutine_token: "own-secret"\n'):
        cfg = _config_at(tmp_path, monkeypatch, text)
        assert bootstrap.ensure_config() is False
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["token"] not in ("change-me", "change-me-too", "", None, "own-secret")
        assert len(raw["token"]) >= 24 and raw["routine_token"] == "own-secret"


def test_both_placeholders_are_replaced_with_two_distinct_secrets(tmp_path, monkeypatch):
    example = (bootstrap.repo_root() / "config" / "config.example.yaml").read_text(
        encoding="utf-8")
    cfg = _config_at(tmp_path, monkeypatch, example)            # copied verbatim, by hand
    assert bootstrap.ensure_config() is False
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert {raw["token"], raw["routine_token"]}.isdisjoint({"change-me", "change-me-too"})
    assert raw["token"] != raw["routine_token"]
    assert bootstrap.ensure_config() is False                   # idempotent: now all secrets
    assert yaml.safe_load(cfg.read_text(encoding="utf-8")) == raw


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
