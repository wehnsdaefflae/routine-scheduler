"""MIGRATION(expires=2026-09-30) guard: a differences-only routine.yaml keeps its config.

0.262.0's template LAYER let a routine's own file record only what it did DIFFERENTLY from its
template. Nothing resolves a template any more (0.269.0 made adoption a one-shot copy), so such
a file would silently lose everything the template used to supply — its permissions, its rules,
its capability mapping — at the next boot. That is the failure this migration exists to prevent,
so the test asserts the EFFECTIVE config is unchanged rather than that some keys moved.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from rsched.config import load_routine
from rsched.migrate_template_layer import migrate_template_layer


def _server(tmp_path: Path, **templates: dict):
    from types import SimpleNamespace

    lib = tmp_path / "lib"
    (lib / "templates").mkdir(parents=True)
    for slug, config in templates.items():
        (lib / "templates" / f"{slug}.md").write_text(
            "---\n" + yaml.safe_dump({"tags": ["a", "b", "c"], "config": config})
            + f"---\n# template: {slug} — does a thing\n\nprose\n", encoding="utf-8")
    home = tmp_path / "routines"
    home.mkdir()
    return SimpleNamespace(routines_home=home, conversations_home=tmp_path / "conv",
                           background_home=tmp_path / "bg", libraries_home=lib)


def _routine(server, slug="r", **raw) -> Path:
    d = server.routines_home / slug
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({"description": "t", **raw}), encoding="utf-8")
    return d


def test_the_templates_contribution_is_written_into_the_routines_own_file(tmp_path):
    server = _server(tmp_path, watcher={"permissions": ["memory", "run-history"],
                                        "rules": ["decision-record"],
                                        "capabilities": {"actions": ["memory_read"],
                                                         "runs": "last"}})
    d = _routine(server, template="watcher", permissions=["shell"])
    assert migrate_template_layer(server) is True

    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert "template" not in raw and "template_except" not in raw
    cfg, _ = load_routine(d)
    # the EFFECTIVE config is what the layer produced — that is the whole contract
    assert sorted(cfg.permissions) == ["memory", "run-history", "shell"]
    assert cfg.rules == ["decision-record"]
    assert cfg.capabilities["actions"] == ["memory_read"]
    assert cfg.capabilities["runs"] == "last"


def test_template_except_is_applied_then_dropped(tmp_path):
    """A subtraction against a layer that no longer exists is meaningless — but the entries it
    removed must not come BACK, so it is applied on the way in and only then discarded."""
    server = _server(tmp_path, watcher={"permissions": ["memory", "run-history"],
                                        "capabilities": {"actions": ["memory_read",
                                                                     "memory_write"]}})
    d = _routine(server, template="watcher", template_except=["run-history", "memory_write"])
    migrate_template_layer(server)

    cfg, _ = load_routine(d)
    assert cfg.permissions == ["memory"]
    assert cfg.capabilities["actions"] == ["memory_read"]
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert "template_except" not in raw


def test_an_unknown_template_only_loses_the_dead_key(tmp_path):
    """Under the layer such a routine was already running on its own config alone, so there is
    nothing to materialize and nothing may change but the key itself."""
    server = _server(tmp_path, watcher={"permissions": ["memory"]})
    d = _routine(server, template="gone", permissions=["shell"])
    migrate_template_layer(server)

    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert "template" not in raw and raw["permissions"] == ["shell"]


def test_a_routine_that_never_adopted_one_is_left_alone(tmp_path):
    server = _server(tmp_path, watcher={"permissions": ["memory"]})
    d = _routine(server, permissions=["shell"])
    before = (d / "routine.yaml").read_text(encoding="utf-8")
    assert migrate_template_layer(server) is False
    assert (d / "routine.yaml").read_text(encoding="utf-8") == before
