"""routine.yaml `connections:` and server `public_url` parse like their siblings and degrade
per key (an unknown provider becomes a problem line + is dropped, never a crash)."""

from __future__ import annotations

import yaml

from rsched.config import load_routine, load_server_config


def _write_routine(tmp_path, data: dict):
    d = tmp_path / "routines" / "r"
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text(
        yaml.safe_dump({"slug": "r", "description": "x", **data}), encoding="utf-8")
    (d / "main.md").write_text("# r\n", encoding="utf-8")
    return load_routine(d)


def test_connections_parse(tmp_path):
    cfg, problems = _write_routine(tmp_path, {"connections": {"notion": "acme"}})
    assert cfg is not None
    assert cfg.connections == {"notion": "acme"}
    assert not any("connections" in p for p in problems)


def test_unknown_provider_dropped(tmp_path):
    cfg, problems = _write_routine(tmp_path, {"connections": {"bogus": "x", "notion": "ok"}})
    assert cfg is not None
    assert cfg.connections == {"notion": "ok"}
    assert any("connections.bogus" in p for p in problems)


def test_bare_connections_null(tmp_path):
    cfg, _ = _write_routine(tmp_path, {"connections": None})
    assert cfg is not None
    assert cfg.connections == {}


def test_public_url_default_and_set(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({}), encoding="utf-8")
    server, _ = load_server_config(p)
    assert server.public_url == ""
    p.write_text(yaml.safe_dump({"public_url": "https://host.ts.net"}), encoding="utf-8")
    server, problems = load_server_config(p)
    assert server.public_url == "https://host.ts.net"
    assert problems == []
