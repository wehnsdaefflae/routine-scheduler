"""MIGRATION(expires=2026-09-30) guard: the library sync goes back to being a routine.

Two pieces of daemon-era instance state have to be cleared, and BOTH fail silently if they
are not: a `library_sync:` key that now reads as an unknown-config warning on every boot, and
an `.archive/library-sync-retired/` tombstone that `adopt_seed_routine` matches BY PREFIX —
which would block the new routine from ever installing, with nothing in the logs saying so.
"""

import yaml

from rsched.bootstrap import adopt_seed_routine
from rsched.config import load_server_config
from rsched.migrate_library_sync import RENAMED_DIR, RETIRED_DIR, migrate_library_sync


def _server(tmp_path, raw):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(raw), encoding="utf-8")
    server, _problems = load_server_config(cfg)
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir(exist_ok=True)
    return server


def test_strips_the_retired_config_key(tmp_path):
    server = _server(tmp_path, {"port": 8321,
                                "library_sync": {"enabled": True, "cron": "0 6 * * *"}})
    # the key is unknown to ServerConfig now, so leaving it warns on every single boot
    _reloaded, problems = load_server_config(server.source)
    assert any("library_sync" in p for p in problems)

    assert migrate_library_sync(server) is True
    raw = yaml.safe_load(server.source.read_text(encoding="utf-8"))
    assert "library_sync" not in raw and raw["port"] == 8321      # siblings untouched
    _after, problems = load_server_config(server.source)
    assert not any("library_sync" in p for p in problems)
    assert migrate_library_sync(server) is False                  # idempotent


def test_archive_tombstone_is_renamed_not_deleted_so_adoption_can_proceed(tmp_path):
    server = _server(tmp_path, {"port": 8321})
    archive = server.routines_home / ".archive" / RETIRED_DIR
    archive.mkdir(parents=True)
    (archive / "LEDGER.md").write_text("real run history from July 2026\n", encoding="utf-8")

    # BEFORE: the prefix match makes adoption a silent no-op
    assert adopt_seed_routine(server.routines_home, "library-sync") is False

    assert migrate_library_sync(server) is True
    moved = server.routines_home / ".archive" / RENAMED_DIR
    assert moved.is_dir() and not archive.exists()
    assert "July 2026" in (moved / "LEDGER.md").read_text(encoding="utf-8")   # kept, not lost
    assert not RENAMED_DIR.startswith("library-sync")     # the whole point of the rename

    # AFTER: the seed installs
    assert adopt_seed_routine(server.routines_home, "library-sync") is True
    assert (server.routines_home / "library-sync" / "main.md").is_file()
    assert migrate_library_sync(server) is False                  # idempotent
