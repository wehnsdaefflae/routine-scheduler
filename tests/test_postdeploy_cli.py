"""`rsched postdeploy`'s thin I/O layer: the command, the record, the exit code (R2428).

The checks themselves are proven in `tests/test_postdeploy.py` over pure inputs. What is left is
everything that touches the world, and it is tested here with the ONE live read stubbed at the
seam `cli_postdeploy._status` keeps it behind — so the suite never reaches an instance and
`tests/production_guard.py` has nothing to object to.

What a run in a SANDBOX cannot do is exercise the real `/api/status` call: the routine that built
this could not read the live config (`Errno 13`) and `/api/settings/*` is 403 for a routine token.
So `_status` is stubbed in every test below and the HTTP transport itself is exercised nowhere —
stated plainly rather than implied, because the alternative is reporting a "0 problems found" that
nothing observed.
"""

import json
from argparse import Namespace

import pytest

from rsched import cli_postdeploy
from rsched.postdeploy import CHECK_NAMES, FAIL, PASS


def _args(**over) -> Namespace:
    return Namespace(**{"url": "", "no_record": False, **over})


@pytest.fixture
def healthy(monkeypatch, cli_server):
    """A live instance that answers with the build the checkout is at, and a fresh backup."""
    version = cli_postdeploy.checkout_version(cli_server.source_repo)
    monkeypatch.setattr(cli_postdeploy, "_status",
                        lambda url, token: {"version": version, "build": "ca142b06 2026-10-23",
                                            "disk_build": "ca142b06 2026-10-23",
                                            "build_skew": False, "server_tz": "Europe/Berlin",
                                            "active_runs": 0})
    # `run_checks` imports `staleness` lazily, so the module attribute IS the seam.
    monkeypatch.setattr("rsched.daemon.backup_watch.staleness",
                        lambda: ("ok", "last complete snapshot 2026-10-23T01:12:00+00:00"))
    return cli_server


def test_the_command_prints_one_line_per_check_and_exits_zero_when_clean(healthy, capsys):
    code = cli_postdeploy.cmd_postdeploy(_args())

    out = capsys.readouterr().out
    assert code == 0
    for name in CHECK_NAMES:
        assert name in out, out                 # the whole fixed checklist, every session
    assert out.count("\n") == len(CHECK_NAMES) + 1      # …plus the summary, and nothing else
    assert "nothing to act on" in out


def test_the_session_is_appended_to_the_instances_own_record(healthy, capsys):
    cli_postdeploy.cmd_postdeploy(_args())
    capsys.readouterr()

    path = healthy.routines_home / cli_postdeploy.RECORD_FILE
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"ts", "url", "version", "build", "verdicts", "details", "summary", "exit"}
    assert set(row["verdicts"]) == set(CHECK_NAMES)
    assert row["exit"] == 0
    assert row["url"].startswith("http://127.0.0.1:")
    # The WHOLE stamp, not a prefix of it. The first version of this built the row by re-parsing
    # `served-identity`'s printed line, and `(\S+)` cut "ca142b06 2026-10-23" at the space — so
    # the record carried a build the operator had never seen. The payload goes to the row
    # directly now, and this is the assertion that says so.
    assert row["build"] == "ca142b06 2026-10-23"


def test_a_second_session_appends_rather_than_replacing(healthy, capsys):
    cli_postdeploy.cmd_postdeploy(_args())
    cli_postdeploy.cmd_postdeploy(_args())
    capsys.readouterr()

    path = healthy.routines_home / cli_postdeploy.RECORD_FILE
    assert len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 2


def test_no_record_prints_the_checklist_and_writes_nothing(healthy, capsys):
    code = cli_postdeploy.cmd_postdeploy(_args(no_record=True))

    assert code == 0
    assert CHECK_NAMES[0] in capsys.readouterr().out
    assert not (healthy.routines_home / cli_postdeploy.RECORD_FILE).exists()


def test_an_instance_that_never_answered_exits_one_and_still_records_the_session(
        monkeypatch, cli_server, capsys):
    """The wrong-machine specimen end to end: nothing answered, so the command goes red, names
    the address it tried, and the session still leaves a trace.
    """
    monkeypatch.setattr(cli_postdeploy, "_status", lambda url, token: None)
    monkeypatch.setattr("rsched.daemon.backup_watch.staleness", lambda: ("ok", "fresh"))

    code = cli_postdeploy.cmd_postdeploy(_args(url="http://192.168.0.128:8321"))

    out, err = capsys.readouterr()
    assert code == 1
    assert "http://192.168.0.128:8321" in out and "--url" in out
    assert "before you walk away" in err
    rows = (cli_server.routines_home / cli_postdeploy.RECORD_FILE).read_text(encoding="utf-8")
    assert '"exit": 1' in rows


def test_a_mistyped_url_is_a_finding_and_not_a_traceback(cli_server, monkeypatch, capsys):
    """`urllib.request.Request` raises ValueError on a url with no scheme. The one moment the
    operator is watching a box he just deployed is the worst possible moment for a traceback.
    """
    monkeypatch.setattr("rsched.daemon.backup_watch.staleness", lambda: ("ok", "fresh"))

    code = cli_postdeploy.cmd_postdeploy(_args(url="127.0.0.1:8321", no_record=True))

    assert code == 1
    assert "no answer from 127.0.0.1:8321" in capsys.readouterr().out


def test_the_default_url_comes_from_the_configs_own_port(healthy, capsys):
    cli_postdeploy.cmd_postdeploy(_args(no_record=True))

    out = capsys.readouterr().out
    assert f"http://127.0.0.1:{healthy.port}" in out
    assert "config bind/port" in out            # the provenance, never only the address


# ---- the ownership walk, against real files on disk ---------------------------------------


def test_the_walk_finds_a_file_owned_by_another_uid_inside_a_clean_home(tmp_path):
    """The 0.373.1 shape as files: the home's own directory is fine and ONE file inside it is
    not. The reference uid is faked past the real one, since a test cannot chown to root.
    """
    home = tmp_path / "routines"
    (home / "sub").mkdir(parents=True)
    (home / "sub" / "auth.json").write_text("{}", encoding="utf-8")

    stats, truncated = cli_postdeploy._owner_stats([home], reference=-999)

    assert not truncated
    assert str(home / "sub" / "auth.json") in [s.path for s in stats]
    assert all(s.exists for s in stats)


def test_the_walk_reports_a_home_that_is_absent_rather_than_claiming_an_owner(tmp_path):
    stats, _ = cli_postdeploy._owner_stats([tmp_path / "never-created"], reference=0)

    assert len(stats) == 1
    assert not stats[0].exists and stats[0].uid == -1


def test_a_walk_that_hits_its_file_cap_says_so(tmp_path, monkeypatch):
    """A clean bill over an unknown remainder is the exact failure this item exists to stop."""
    monkeypatch.setattr(cli_postdeploy, "WALK_FILE_CAP", 2)
    home = tmp_path / "routines"
    home.mkdir()
    for i in range(5):
        (home / f"f{i}").write_text("x", encoding="utf-8")

    _stats, truncated = cli_postdeploy._owner_stats([home], reference=-999)

    assert truncated


def test_the_reference_uid_is_the_homes_owner_and_not_whoever_ran_the_command(cli_server):
    """NOT `os.getuid()`: a `docker compose exec` without `-u mark` runs this as root, and then
    every root-owned file — the defect — compares equal and the check passes on exactly the
    instance it exists to catch.
    """
    assert cli_postdeploy._reference_uid(cli_server) == cli_server.routines_home.stat().st_uid


# ---- what the checkout says it is ----------------------------------------------------------


def test_the_checkout_version_is_read_as_text_from_the_tree_that_was_deployed(tmp_path):
    pkg = tmp_path / "src" / "rsched"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""doc"""\n__version__ = "0.444.0"\n', encoding="utf-8")

    assert cli_postdeploy.checkout_version(tmp_path) == "0.444.0"


@pytest.mark.parametrize("repo", [None, "missing"])
def test_an_unreadable_checkout_version_is_empty_and_never_raises(tmp_path, repo):
    target = None if repo is None else tmp_path / repo
    assert cli_postdeploy.checkout_version(target) == ""


def test_the_homes_checked_are_the_ones_the_daemon_writes(cli_server):
    homes = [str(h) for h in cli_postdeploy._homes(cli_server)]

    assert str(cli_server.routines_home) in homes
    assert str(cli_server.libraries_home) in homes
    assert len(homes) == len(set(homes))        # deduped: one home checked once


def test_the_subcommand_is_reachable_through_the_real_argv_parser(healthy, capsys):
    """Through `cli.main`, not by calling the body: a command body that works and a parser that
    never registers it is a command the operator cannot type. `--no-record` is parsed here too,
    so the flags are exercised as argparse sees them.
    """
    from rsched import cli

    code = cli.main(["postdeploy", "--no-record"])

    assert code == 0
    assert CHECK_NAMES[0] in capsys.readouterr().out


def test_an_unreadable_backup_record_turns_the_whole_command_red(monkeypatch, cli_server,
                                                                 capsys, tmp_path):
    """F602 end to end through the command, driven by the REAL `backup_watch.staleness`: no
    stamp under any root → `unknown` → FAIL → exit 1. Nineteen nights passed because nothing
    anywhere read this, so the absence of the record has to be what makes the session red.
    """
    monkeypatch.setattr(cli_postdeploy, "_status",
                        lambda url, token: {"version": "0.444.0", "build": "", "disk_build": "",
                                            "build_skew": False, "server_tz": "UTC"})
    monkeypatch.setenv("RSCHED_MIRROR", str(tmp_path / "not-mounted"))
    monkeypatch.setattr("rsched.daemon.backup_watch.DEFAULT_ROOT", str(tmp_path / "nor-this"))

    code = cli_postdeploy.cmd_postdeploy(_args(no_record=True))

    out = capsys.readouterr().out
    assert code == 1
    assert f"{FAIL}  backup-freshness" in out
    assert "ABSENCE" in out
    assert f"{PASS}  served-identity" in out    # and the rest of the checklist still reported
