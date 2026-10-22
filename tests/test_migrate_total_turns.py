"""The one-shot rewrite of a SCAFFOLD-WRITTEN `max_total_turns: -1` to `0` (derive).

MIGRATION(expires=2026-12-31). F564/D155's run-spanning cap shipped inert on 28 of 33 routines
because the scaffold had written an explicit `-1` and an explicit value always wins. The
operator chose the migration (R2782/R2790) — *"rewrites a scaffold-written -1 to 0 on all 28
existing configs"* — and the load-bearing word is **scaffold**: `-1` is also a legitimate
deliberate uncapping, which `turnwindow` honours and which is unrecoverable once overwritten.

So these tests exercise the discriminator against REAL git history rather than mocking it: a
line written once and never touched is the scaffold's, a line a second commit touched is not.
"""
from __future__ import annotations

from types import SimpleNamespace

import yaml

from conftest import git_in
from helpers import routine_yaml
from rsched import migrate_total_turns
from rsched.config import load_routine
from rsched.engine.turnwindow import total_turn_cap
from rsched.paths import read_json


def _server(tmp_path):
    homes = {k: tmp_path / k for k in ("routines", "conversations", "background")}
    for home in homes.values():
        home.mkdir()
    return SimpleNamespace(routines_home=homes["routines"],
                           conversations_home=homes["conversations"],
                           background_home=homes["background"])


def _routine(home, slug, *, total=-1, max_turns=200, scaffolded=None):
    """A routine whose history ends with `max_total_turns: total`.

    `scaffolded` is the value the SCAFFOLD committed. Left None the scaffold wrote `total`
    itself and nothing ever touched that line again — the shape all 28 live configs have
    (measured 2026-10-22 with `git log -G`). Given a different value, a SECOND commit changes
    the line to `total`, which is what a deliberate choice looks like in history: somebody
    edited that very line.
    """
    d = home / slug
    d.mkdir()
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")
    raw = {"name": slug, "slug": slug, "description": "d",
           "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "skip"},
           "budgets": {"max_turns": max_turns,
                       "max_total_turns": total if scaffolded is None else scaffolded,
                       "max_wall_clock_min": 45}}
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    git_in(d, "init", "-q")
    git_in(d, "add", "-A")
    git_in(d, "commit", "-q", "-m", f"scaffold {slug} from workflow general-task")
    if scaffolded is not None:
        raw["budgets"]["max_total_turns"] = total
        (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        git_in(d, "add", "-A")
        git_in(d, "commit", "-q", "-m", "routine.yaml edit via web (budgets)")
    return d


def test_the_scaffolds_inert_minus_one_starts_deriving(tmp_path):
    """Half B of F564 reaches the routine: `0` derives 3x the window, so a run that resumes
    forever now meets a cumulative backstop. The measured shape on the live instance —
    `max_turns: 500`, `max_total_turns: -1`, one commit on that line."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "builderish", max_turns=500)
    assert total_turn_cap(500, -1) == -1                   # before: the backstop is inert

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == 0
    assert record["migrated"] == {"routines/builderish": 1500}
    cfg, problems = load_routine(d)
    assert cfg is not None and not [p for p in problems if "max_total" in p]
    assert cfg.budgets["max_total_turns"] == 0             # resolved to 1500 at run time
    assert total_turn_cap(500, 0) == 1500


def test_a_deliberate_uncapping_is_left_alone(tmp_path):
    """The one thing the migration must not do. Here the scaffold wrote a 900-turn cap and a
    later commit changed that line to `-1` — a person chose unlimited, and `turnwindow`
    deliberately honours it. Once rewritten that intent is gone from the file for good, so the
    doubt resolves in favour of leaving it.

    Note what does NOT count as a touch, and should not: a later commit that rewrites the file
    while leaving this line byte-identical (a `max_wall_clock_min` edit through the web form)
    changes nothing about who chose the `-1`."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "deliberate", total=-1, scaffolded=900)

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == -1
    assert record["migrated"] == {}
    assert "deliberate uncapping" in record["kept"]["routines/deliberate"]


def test_an_unrelated_later_edit_does_not_protect_the_scaffolds_minus_one(tmp_path):
    """The discriminator has to be about THAT LINE, not about the file. Live configs are edited
    through the web form all the time (`routine.yaml edit via web (budgets)` appears in two of
    the 28 histories), and if any later commit counted, the migration would skip exactly the
    routines whose budgets someone has looked at — which is most of them."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "edited-elsewhere", max_turns=150)
    raw = routine_yaml(d)
    raw["budgets"]["max_wall_clock_min"] = 90           # a different line entirely
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    git_in(d, "add", "-A")
    git_in(d, "commit", "-q", "-m", "routine.yaml edit via web (budgets)")

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == 0
    assert record["migrated"] == {"routines/edited-elsewhere": 450}


def test_an_unlimited_window_gains_nothing_and_is_named(tmp_path):
    """3x unlimited is not a number (`total_turn_cap`), so rewriting a routine whose
    `max_turns` is -1 would be a no-op the record would wrongly count as delivered."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "boundless", max_turns=-1)

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == -1
    assert record["migrated"] == {}
    assert "unlimited" in record["kept"]["routines/boundless"]
    assert total_turn_cap(-1, 0) == -1


def test_an_explicit_positive_cap_is_not_this_migrations_business(tmp_path):
    server = _server(tmp_path)
    d = _routine(server.routines_home, "capped", total=900)

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == 900
    assert record["migrated"] == {} and record["kept"] == {}


def test_a_non_repo_config_keeps_its_minus_one(tmp_path):
    """No history, no discriminator. An inert backstop on one routine costs less than
    silently removing an intended one, so the doubt is recorded rather than resolved."""
    server = _server(tmp_path)
    d = server.routines_home / "historyless"
    d.mkdir()
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "historyless", "slug": "historyless", "description": "d",
         "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "skip"},
         "budgets": {"max_turns": 100, "max_total_turns": -1}}), encoding="utf-8")

    record = migrate_total_turns.run_migration(server)

    assert routine_yaml(d)["budgets"]["max_total_turns"] == -1
    assert "history could not be read" in record["kept"]["routines/historyless"]


def test_a_server_without_the_extra_homes_does_not_break_the_boot(tmp_path):
    """A boot migration is handed whatever `load_server_config` returns, and
    `tests/test_bootstrap.py`'s fresh-container boot hands it a four-attribute stand-in with no
    `conversations_home`. Reading that as an attribute made the WHOLE DAEMON BOOT die on an
    AttributeError inside a migration that had nothing to migrate — it reddened release gate
    1792680349875750345 on both parameters of that test."""
    minimal = SimpleNamespace(routines_home=tmp_path / "routines")
    minimal.routines_home.mkdir()
    _routine(minimal.routines_home, "lonely", max_turns=100)

    record = migrate_total_turns.run_migration(minimal)

    assert record["migrated"] == {"routines/lonely": 300}


def test_it_runs_once_and_covers_all_three_homes(tmp_path):
    """A conversation and a background task are routines on disk too — and the record is the
    guard: while it exists the migration does nothing, so a second boot cannot re-rewrite a
    line the operator has since set back to -1 on purpose."""
    server = _server(tmp_path)
    _routine(server.routines_home, "r1")
    _routine(server.conversations_home, "c1")
    _routine(server.background_home, "b1")

    record = migrate_total_turns.run_migration(server)
    assert set(record["migrated"]) == {"routines/r1", "conversations/c1", "background/b1"}

    again = _routine(server.routines_home, "later")
    assert migrate_total_turns.run_migration(server) == {}
    assert routine_yaml(again)["budgets"]["max_total_turns"] == -1
    stored = read_json(server.routines_home / migrate_total_turns.RECORD)
    assert set(stored["migrated"]) == set(record["migrated"])
