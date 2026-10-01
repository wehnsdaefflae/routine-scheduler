"""The one-shot fold of `schedule.disabled` into `enabled` (MIGRATION(expires=2026-11-15)):
every routine.yaml in the three homes ends with one off switch, and no routine's switch
changes — the old loader's verdict and the new one's agree, file by file."""
from __future__ import annotations

import json
from types import SimpleNamespace

import yaml

from helpers import routine_yaml
from rsched import libgit, migrate_enabled
from rsched.config import load_routine


def _server(tmp_path):
    homes = {k: tmp_path / k for k in ("routines", "conversations", "background")}
    for home in homes.values():
        home.mkdir()
    return SimpleNamespace(routines_home=homes["routines"],
                           conversations_home=homes["conversations"],
                           background_home=homes["background"])


def _routine(home, slug, **top):
    d = home / slug
    d.mkdir()
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")
    raw = {"name": slug, "slug": slug, "description": "d",
           "schedule": {"cron": "0 7 * * 1", "tz": "UTC", "catchup": "skip"}}
    for key, value in top.items():
        if key == "disabled":
            raw["schedule"]["disabled"] = value
        else:
            raw[key] = value
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return d


def test_every_switch_reads_the_same_before_and_after(tmp_path):
    """`disabled: true` was off whatever `enabled` said; anything else left `enabled` to decide.
    The fold keeps both rules — no routine starts or stops firing because of it."""
    server = _server(tmp_path)
    home = server.routines_home
    cases = {   # slug → (top-level fields, the switch the OLD loader read)
        "off-by-schedule": ({"enabled": True, "disabled": True}, False),
        "off-both-ways": ({"enabled": False, "disabled": True}, False),
        "off-by-enabled": ({"enabled": False, "disabled": False}, False),
        "on-implicitly": ({"disabled": False}, True),
        "on-explicitly": ({"enabled": True, "disabled": False}, True),
        "garbage-disabled": ({"disabled": "yes"}, True),   # only a literal true was ever off
    }
    dirs = {slug: _routine(home, slug, **top) for slug, (top, _was) in cases.items()}

    record = migrate_enabled.run_migration(server)

    for slug, (_top, was) in cases.items():
        raw = routine_yaml(dirs[slug])
        assert "disabled" not in raw["schedule"], slug
        assert raw["enabled"] is was, slug
        cfg, problems = load_routine(dirs[slug])
        assert cfg is not None and cfg.enabled is was, slug
        assert not [p for p in problems if p.startswith("schedule.")], (slug, problems)
    assert record["migrated"] == {f"routines/{slug}": was for slug, (_t, was) in cases.items()}
    assert record["failed"] == {}


def test_a_file_with_one_switch_already_is_left_byte_for_byte(tmp_path):
    server = _server(tmp_path)
    d = _routine(server.routines_home, "clean", enabled=False)
    before = (d / "routine.yaml").read_bytes()
    record = migrate_enabled.run_migration(server)
    assert (d / "routine.yaml").read_bytes() == before
    assert record["migrated"] == {}


def test_all_three_homes_are_folded_and_each_change_is_committed(tmp_path):
    server = _server(tmp_path)
    routine = _routine(server.routines_home, "r", disabled=True)
    libgit.init_repo(routine, first_commit="init")
    conv = _routine(server.conversations_home, "c-1", kind="conversation", disabled=False)
    task = _routine(server.background_home, "bg-1", disabled=True)
    (server.routines_home / ".clarify-x").mkdir()              # a workspace, never a routine
    (server.routines_home / ".clarify-x" / "routine.yaml").write_text(
        yaml.safe_dump({"schedule": {"disabled": True}}), encoding="utf-8")

    record = migrate_enabled.run_migration(server)

    assert record["migrated"] == {"routines/r": False, "conversations/c-1": True,
                                  "background/bg-1": False}
    assert (routine_yaml(conv)["enabled"], routine_yaml(task)["enabled"]) == (True, False)
    assert "disabled" in yaml.safe_load(
        (server.routines_home / ".clarify-x" / "routine.yaml").read_text(encoding="utf-8"))[
        "schedule"]
    subject = libgit.git(routine, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "off switch: schedule.disabled folded into enabled: False"
    assert libgit.git(routine, "status", "--porcelain").stdout.strip() == ""


def test_it_runs_once_and_records_what_it_could_not_read(tmp_path):
    server = _server(tmp_path)
    good = _routine(server.routines_home, "good", disabled=True)
    broken = server.routines_home / "broken"
    broken.mkdir()
    (broken / "routine.yaml").write_text("schedule: {disabled: [unclosed\n", encoding="utf-8")

    record = migrate_enabled.run_migration(server)

    assert routine_yaml(good)["enabled"] is False
    assert list(record["failed"]) == ["routines/broken"]
    stored = json.loads((server.routines_home / migrate_enabled.RECORD).read_text("utf-8"))
    assert stored["migrated"] == {"routines/good": False}
    # once recorded it never runs again, so a later hand edit is the loader's to report
    late = _routine(server.routines_home, "late", disabled=True)
    assert migrate_enabled.run_migration(server) == {}
    assert routine_yaml(late)["schedule"]["disabled"] is True
