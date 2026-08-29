"""MIGRATION(expires=2026-09-30) guard: the clarification template declares `kind: template`.

Without the marker an existing instance's template silently becomes runnable and archivable,
because the guards read the declared kind now instead of comparing slugs. Nothing else would
ever write it.
"""

import pytest
import yaml
from fastapi import HTTPException

from rsched.config import ServerConfig, load_routine
from rsched.migrate_template_kind import migrate_template_kind
from rsched.web.routines_common import guard_template


@pytest.fixture
def instance(tmp_path):
    d = tmp_path / "routines" / "clarification"
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text("name: Routine clarification\nslug: clarification\n"
                                    "enabled: false\n", encoding="utf-8")
    (d / "main.md").write_text(
        "# template\n\nWhat is configurable here:\n"
        "- **Budgets** — caps.\n"
        "- **Traits** — practice modules copied into every session.\n", encoding="utf-8")
    return ServerConfig(routines_home=tmp_path / "routines"), d


def test_marks_the_template_and_repairs_the_retired_line(instance):
    server, d = instance
    assert migrate_template_kind(server) is True
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["kind"] == "template"
    body = (d / "main.md").read_text(encoding="utf-8")
    assert "**Traits**" not in body and "**Rules**" in body
    assert "- **Budgets** — caps." in body          # only the stale line was touched
    assert migrate_template_kind(server) is False   # idempotent


def test_the_marker_is_what_makes_the_guards_bite(instance):
    server, d = instance
    cfg, _ = load_routine(d)
    assert cfg is not None
    guard_template(cfg, "unprotected")              # no marker yet → no refusal

    migrate_template_kind(server)
    cfg, _ = load_routine(d)
    assert cfg is not None and cfg.kind == "template"
    with pytest.raises(HTTPException) as exc:
        guard_template(cfg, "it never runs directly")
    assert exc.value.status_code == 403


def test_an_ordinary_routine_is_never_protected(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    (d / "routine.yaml").write_text("name: R\nslug: r\n", encoding="utf-8")
    cfg, _ = load_routine(d)
    assert cfg is not None and cfg.kind == ""
    guard_template(cfg, "must not raise")
