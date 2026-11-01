"""The one-shot drop of the reserved utils no library has, from `capabilities.utils`.

MIGRATION(expires=2026-12-31) — narration in `rsched/migrate_dead_reserved_utils.py`. The
`fau-mail`/`gmail` → `mail` consolidation left two dead names in six installed `routine.yaml`
files, and every run of those six opened with two FAIL rows from
`readmodels/surface_caps._absent_util_node`. `routine.yaml` is the operator's config and no run
may write it, so the daemon's boot is the only writer that can reach them; he authorized exactly
that (D228, option (a)): *"drops the two dead verbs from every installed routine.yaml's
capabilities.utils, leaving mail:send"*.

So these tests pin the three things that answer makes load-bearing: the drop REMOVES and never
adds, it leaves the rest of the file alone, and it is not a list of the six.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from conftest import git_in
from helpers import routine_yaml
from rsched import migrate_dead_reserved_utils as migration
from rsched.config import load_routine
from rsched.paths import read_json

#: The live shape, from `freelance-hunter/routine.yaml` on 2026-11-01: the live verb among the
#: two dead ones, beside an unrelated reserved util that must survive untouched.
LIVE_SIX = ["browser-session", "fau-mail:send", "gmail:send", "mail:send"]


def _server(tmp_path):
    homes = {k: tmp_path / k for k in ("routines", "conversations", "background")}
    for home in homes.values():
        home.mkdir()
    return SimpleNamespace(routines_home=homes["routines"],
                           conversations_home=homes["conversations"],
                           background_home=homes["background"])


def _config(slug: str, utils: list[str] | None, *, caps: bool = True) -> dict:
    raw: dict = {"name": slug, "slug": slug, "description": "d",
                 "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "skip"},
                 "permissions": ["outbound-mail"],
                 "budgets": {"max_turns": 120, "max_wall_clock_min": 45}}
    if caps:
        raw["capabilities"] = {"confirm": "always", "runs": "last", "actions": []}
        if utils is not None:
            raw["capabilities"]["utils"] = utils
    raw["enabled"] = True
    return raw


#: `_routine`'s "the live six's shape" — distinct from `None`, which means the `capabilities`
#: block carries NO `utils:` key at all (a real shape: a routine that reserves no util).
_LIVE_SHAPE = object()


def _routine(home, slug, utils: object = _LIVE_SHAPE, *, caps: bool = True, repo: bool = True):
    """A routine dir whose `capabilities.utils` is `utils` (default: the live six's shape)."""
    d = home / slug
    d.mkdir(parents=True)
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")
    (d / "routine.yaml").write_text(
        yaml.safe_dump(_config(slug, LIVE_SIX if utils is _LIVE_SHAPE else utils, caps=caps),
                       sort_keys=False),
        encoding="utf-8")
    if repo:
        git_in(d, "init", "-q")
        git_in(d, "add", "-A")
        git_in(d, "commit", "-q", "-m", f"scaffold {slug}")
    return d


def test_the_two_dead_verbs_go_and_mail_send_stays(tmp_path):
    """The shape all six live files have. `mail:send` and the unrelated reserved util survive;
    the two names no util in the catalog answers to are gone, so the run's preflight stops
    reporting two FAIL rows it can do nothing about."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "freelance-hunterish")

    record = migration.run_migration(server)

    assert routine_yaml(d)["capabilities"]["utils"] == ["browser-session", "mail:send"]
    assert record["dropped"] == {"routines/freelance-hunterish": ["fau-mail:send", "gmail:send"]}
    assert record["no_live_mail"] == [] and record["failed"] == {}
    cfg, problems = load_routine(d)
    assert cfg is not None and not problems
    assert cfg.capabilities["utils"] == ["browser-session", "mail:send"]


def test_it_commits_the_drop_in_the_routines_own_repo(tmp_path):
    """A config changed on disk but not in its history is a change nobody can see. The commit
    is scoped to `routine.yaml`, like every sibling migration's."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "committed")

    migration.run_migration(server)

    assert git_in(d, "status", "--porcelain").stdout.strip() == ""
    assert "drop the reserved utils no library has" in git_in(d, "log", "-1", "--format=%s").stdout


def test_everything_else_in_the_file_is_left_alone(tmp_path):
    """Only those entries move. The whole document is compared key by key, because the writer is
    a full dump: a migration that silently reordered or dropped a key the operator wrote would
    be worse than the two FAIL rows it removes."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "faithful")
    before = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))

    migration.run_migration(server)

    after = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert list(after) == list(before)                       # key ORDER, not just the keys
    assert list(after["capabilities"]) == list(before["capabilities"])
    before["capabilities"]["utils"] = ["browser-session", "mail:send"]
    assert after == before


def test_a_bare_dead_name_goes_too(tmp_path):
    """A bare `fau-mail` reserves every verb of a util that does not exist either, and reports
    the very same absent row — so the drop keys on the util NAME, not on the two literals."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "bare", ["fau-mail", "gmail", "mail:send"])

    record = migration.run_migration(server)

    assert routine_yaml(d)["capabilities"]["utils"] == ["mail:send"]
    assert record["dropped"] == {"routines/bare": ["fau-mail", "gmail"]}


def test_it_is_not_a_list_of_the_six(tmp_path):
    """Every installed routine.yaml that names a dead util is reached, so a SEVENTH file written
    while this shipped does not survive the migration."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "the-seventh", ["gmail:send", "mail:send"])

    record = migration.run_migration(server)

    assert routine_yaml(d)["capabilities"]["utils"] == ["mail:send"]
    assert record["dropped"] == {"routines/the-seventh": ["gmail:send"]}


def test_a_file_left_without_the_live_verb_is_named_never_given_one(tmp_path):
    """The migration REMOVES and never adds. A config whose only mail verb was a dead one loses
    it — a name no util answers to grants nothing — and is NAMED, so the operator can grant
    `mail:send` himself rather than find the engine granted it for him."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "deadonly", ["gmail:send"])

    record = migration.run_migration(server)

    assert routine_yaml(d)["capabilities"]["utils"] == []
    assert record["no_live_mail"] == ["routines/deadonly"]


@pytest.mark.parametrize("utils", [["browser-session", "mail:send"], [], None])
def test_a_file_naming_no_dead_util_is_left_byte_for_byte(tmp_path, utils):
    """The hand-correction guarantee this family of migrations rests on — and the second half of
    idempotence: a corrected file stays as it is, to the byte."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "clean", utils)
    before = (d / "routine.yaml").read_bytes()

    record = migration.run_migration(server)

    assert (d / "routine.yaml").read_bytes() == before
    assert record["dropped"] == {} and record["failed"] == {}


def test_a_config_without_a_capabilities_block_is_not_its_business(tmp_path):
    server = _server(tmp_path)
    d = _routine(server.routines_home, "capless", caps=False)
    before = (d / "routine.yaml").read_bytes()

    assert migration.run_migration(server)["dropped"] == {}
    assert (d / "routine.yaml").read_bytes() == before


def test_a_second_pass_over_a_corrected_file_changes_nothing(tmp_path):
    """Idempotence as the daemon meets it: the record stops the second boot dead, and the drop
    itself is a no-op anyway once the dead names are gone (the record is removed here to prove
    the second half on its own)."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "twice")

    assert migration.run_migration(server)["dropped"]
    (server.routines_home / migration.RECORD).unlink()
    after_first = (d / "routine.yaml").read_bytes()

    assert migration.run_migration(server)["dropped"] == {}
    assert (d / "routine.yaml").read_bytes() == after_first


def test_an_unparseable_config_is_recorded_not_skipped_silently(tmp_path):
    """A migration that cannot do its job says so. The file keeps its FAIL rows and its bytes,
    and the record is where a person sees that it did — with git's own words, not a bare class
    name: the type alone ('ParserError') does not say WHICH line a person has to go and fix."""
    server = _server(tmp_path)
    d = _routine(server.routines_home, "broken")
    (d / "routine.yaml").write_text("capabilities: [unclosed\n", encoding="utf-8")
    before = (d / "routine.yaml").read_bytes()

    record = migration.run_migration(server)

    why = record["failed"]["routines/broken"]
    assert why.startswith("ParserError:") and "line 1, column 15" in why   # a yaml.YAMLError
    assert record["dropped"] == {}
    assert (d / "routine.yaml").read_bytes() == before


def test_a_server_without_the_extra_homes_does_not_break_the_boot(tmp_path):
    """A boot migration is handed whatever `load_server_config` returns, and
    `tests/test_bootstrap.py`'s fresh-container boot hands it a stand-in with neither extra
    home. Reading one as an attribute would make the WHOLE DAEMON BOOT die on an AttributeError
    inside a migration that had nothing to do — it reddened release gate 1792680349875750345
    for `migrate_total_turns`."""
    minimal = SimpleNamespace(routines_home=tmp_path / "routines")
    minimal.routines_home.mkdir()
    _routine(minimal.routines_home, "lonely")

    record = migration.run_migration(minimal)

    assert record["dropped"] == {"routines/lonely": ["fau-mail:send", "gmail:send"]}


def test_the_daemons_boot_sequence_actually_runs_it(tmp_path, monkeypatch):
    """The half a test on the module alone cannot see: a migration nothing CALLS is a silent
    no-op that passes its own suite. So the real `cmd_daemon` boot is driven here, with the
    live six's shape on disk, exactly as `tests/test_bootstrap.py` drives it — which is also
    the fresh-container stand-in that kills the whole boot on a hard `server.conversations_home`.
    """
    from rsched import bootstrap, cli_daemon

    lib = tmp_path / "lib"
    server = SimpleNamespace(libraries_home=lib, libraries_remote="", bind="127.0.0.1",
                             port=8321, routines_home=tmp_path / "routines",
                             permissions_home=lib / "permissions")
    server.routines_home.mkdir()
    d = _routine(server.routines_home, "booted")
    monkeypatch.setattr(bootstrap, "ensure_config", lambda: False)
    monkeypatch.setattr(cli_daemon, "load_server_config", lambda: (server, []))
    # `migrate_enabled` is patched out for the same reason `tests/test_bootstrap.py` patches it
    # out, and it is not cosmetic: that migration still reads `server.conversations_home` as a
    # hard ATTRIBUTE (migrate_enabled.py:45), so against this stand-in it kills the whole boot
    # with an AttributeError before any later step runs — measured here on 2026-11-01. It is a
    # defect of that file, not of this one (reported separately); patching it is what lets this
    # test reach the step it is about.
    monkeypatch.setattr("rsched.migrate_enabled.run_migration", lambda s: {})
    monkeypatch.setattr("rsched.web.app.create_app", lambda s: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    assert cli_daemon.cmd_daemon(None) == 0

    assert routine_yaml(d)["capabilities"]["utils"] == ["browser-session", "mail:send"]
    assert read_json(server.routines_home / migration.RECORD)["dropped"] == {
        "routines/booted": ["fau-mail:send", "gmail:send"]}


def test_it_runs_once_and_covers_all_three_homes(tmp_path):
    """A conversation and a background task are routines on disk too — and the record is the
    guard: while it exists the migration does nothing, so a name the operator has since put
    back on purpose is not dropped a second time."""
    server = _server(tmp_path)
    _routine(server.routines_home, "r1")
    _routine(server.conversations_home, "c1")
    _routine(server.background_home, "b1")
    _routine(server.routines_home, ".clarify-workspace")      # dot-prefixed: never touched

    record = migration.run_migration(server)
    assert set(record["dropped"]) == {"routines/r1", "conversations/c1", "background/b1"}
    assert routine_yaml(server.routines_home / ".clarify-workspace")[
        "capabilities"]["utils"] == LIVE_SIX

    later = _routine(server.routines_home, "later")
    assert migration.run_migration(server) == {}
    assert routine_yaml(later)["capabilities"]["utils"] == LIVE_SIX
    stored = read_json(server.routines_home / migration.RECORD)
    assert set(stored["dropped"]) == set(record["dropped"])
