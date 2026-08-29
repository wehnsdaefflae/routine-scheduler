"""MIGRATION(expires=2026-09-30) guard: the library sync goes back to being a routine.

One piece of daemon-era instance state has to be cleared, and it fails silently if it is
not: a `library_sync:` key that now reads as an unknown-config warning on every boot.
"""

import yaml

from rsched.config import load_server_config
from rsched.migrate_library_sync import migrate_library_sync


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
