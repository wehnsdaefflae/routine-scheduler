"""The daemon runs the post-deploy checklist itself, a short while after boot (F854).

`rsched postdeploy` shipped in 0.445.0 and nothing ever ran it: `grep -rn "postdeploy.jsonl"
src/` found exactly one writer, the CLI module, reached only when a person types the command.
Meanwhile the restart sentinel path — the one this instance takes several times a week — exits
for the supervisor and verifies nothing. Measured cost: the daemon came up on 0.520.0 at
2026-11-08T17:56:26 while the record's newest row was still 2026-11-06T10:00:22, whose own
backup-freshness check was a FAIL nothing re-checked for sixty hours.

The operator chose the shape verbatim: "the daemon runs the checks itself a short while after
boot (in-process, served-identity hitting its own port once it answers) and appends the row — no
human step ever again". So these tests pin that a boot leaves a row with nobody typing anything,
that the row is the SAME shape the command writes (or the two histories stop being comparable),
that a FAIL reaches a surface the console reads, and that the checks themselves are never
re-implemented.
"""

import json

from helpers import health_events, tmp_server
from rsched import cli_postdeploy, postdeploy
from rsched.daemon import postdeploy_watch
from rsched.postdeploy import FAIL, PASS, Check

STATUS = {"version": "9.9.9", "build": "abc1234", "server_tz": "Europe/Berlin",
          "active_runs": {}, "build_skew": False}


def _checks(verdict=PASS):
    return [Check(name, verdict, f"{name} detail") for name in postdeploy.CHECK_NAMES]


def _record(server):
    path = server.routines_home / cli_postdeploy.RECORD_FILE
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_a_boot_leaves_a_record_row_with_nobody_typing_anything(tmp_path, monkeypatch):
    """The whole item: before this, the only writer of that file was a human at a terminal."""
    server = tmp_server(tmp_path)
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: (_checks(), dict(STATUS)))

    checks = postdeploy_watch.run_now(server)

    assert checks is not None
    rows = _record(server)
    assert len(rows) == 1
    assert rows[0]["trigger"] == "daemon boot"


def test_the_row_is_the_shape_the_typed_command_writes(tmp_path, monkeypatch):
    """`rsched postdeploy`'s history and the automatic rows have to be comparable — comparing
    them IS the point of a record. So the row comes from `postdeploy.record_row`, with the
    trigger added, and never from a second hand-built dict.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: (_checks(), dict(STATUS)))

    postdeploy_watch.run_now(server)

    row = _record(server)[0]
    typed = postdeploy.record_row(row["ts"], row["url"], dict(STATUS), _checks())
    assert {k: v for k, v in row.items() if k != "trigger"} == typed


def test_the_checks_are_the_commands_own_and_not_reimplemented(tmp_path, monkeypatch):
    """A second implementation of the checklist is two checklists that disagree. The watcher
    calls `cli_postdeploy.run_checks`, so patching that seam is enough to control every verdict.
    """
    server = tmp_server(tmp_path)
    seen = {}

    def spy(srv, url, source):
        seen.update(url=url, source=source)
        return _checks(), dict(STATUS)

    monkeypatch.setattr(cli_postdeploy, "run_checks", spy)

    postdeploy_watch.run_now(server)

    assert seen["url"] == f"http://127.0.0.1:{server.port}"
    assert seen["source"] == "daemon boot", "the row says which question it answers"


def test_it_waits_for_its_own_port_instead_of_recording_its_own_boot(tmp_path, monkeypatch):
    """The check reads its own HTTP port, so asking too early would record a FAIL about the
    boot that is still happening. A status of None means nothing answered yet: retry, and only
    then record.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(postdeploy_watch, "RETRY_S", 0.0)
    answers = [(_checks(FAIL), None), (_checks(FAIL), None), (_checks(), dict(STATUS))]
    calls = []

    def answering(srv, url, source):
        calls.append(url)
        return answers[len(calls) - 1]

    monkeypatch.setattr(cli_postdeploy, "run_checks", answering)

    checks = postdeploy_watch.run_now(server)

    assert len(calls) == 3, "it retried until the port answered"
    assert [c.verdict for c in checks] == [PASS] * 4
    assert len(_record(server)) == 1, "one row per boot, not one per attempt"


def test_a_port_that_never_answers_is_recorded_rather_than_retried_forever(tmp_path,
                                                                           monkeypatch):
    """An instance whose own port never answers is a finding, and the checklist's own
    `served-identity` FAIL says it. Recording it is what makes the silence mean something.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(postdeploy_watch, "RETRY_S", 0.0)
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: ([postdeploy.served_identity(url, None, source),
                                                   *_checks()[1:]], None))

    postdeploy_watch.run_now(server)

    rows = _record(server)
    assert len(rows) == 1
    assert rows[0]["verdicts"]["served-identity"] == FAIL


def test_a_failed_check_reaches_a_surface_the_console_reads(tmp_path, monkeypatch):
    """The record is a file no console shows, which is how a backup-freshness FAIL sat
    unexamined for sixty hours. `postdeploy_failed` is on the blocked-work fold.
    """
    server = tmp_server(tmp_path)
    failing = [Check(postdeploy.CHECK_NAMES[0], PASS, "ok"),
               Check(postdeploy.CHECK_NAMES[1], FAIL, "the daemon serves 0.1.0, checkout 0.2.0"),
               *_checks()[2:]]
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: (failing, dict(STATUS)))

    postdeploy_watch.run_now(server)

    events = health_events(server.routines_home, event="postdeploy_failed")
    assert len(events) == 1
    assert "build-skew" in events[0]["checks"]
    assert "0.2.0" in events[0]["detail"]


def test_a_clean_checklist_files_no_event(tmp_path, monkeypatch):
    """Zero is the good reading and must be silent: an event per boot would make the fold's
    blocked list useless within a week.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: (_checks(), dict(STATUS)))

    postdeploy_watch.run_now(server)

    assert health_events(server.routines_home, event="postdeploy_failed") == []


def test_the_event_is_on_the_blocked_work_fold_and_in_the_vocabulary():
    """A health event the console is not compiled for is read by nobody — `trigger_capped` went
    unread for weeks that way.
    """
    from rsched import health_vocab
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert "postdeploy_failed" in BLOCKED_EVENTS
    assert BLOCKED_EVENTS["postdeploy_failed"].strip()
    assert "postdeploy_failed" in health_vocab.EVENTS


def test_a_record_that_cannot_be_written_does_not_take_the_instance_down(tmp_path, monkeypatch):
    """The checks are the product; the record is best-effort. A watcher that raised here would
    be a deploy check that breaks the deploy.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(cli_postdeploy, "run_checks",
                        lambda srv, url, source: (_checks(), dict(STATUS)))
    monkeypatch.setattr(postdeploy_watch, "append_jsonl",
                        lambda path, row: (_ for _ in ()).throw(OSError("read-only fs")))

    assert postdeploy_watch.run_now(server) is not None


def test_the_checklist_is_launched_from_the_lifespan_and_stopped_with_it():
    """A watcher nothing calls is prose. It runs from the app lifespan — the moment this process
    starts serving, which is what the checklist asks about — and is cancelled on shutdown like
    every other lifespan task, so a shutdown during its wait leaves no row.
    """
    from pathlib import Path

    from rsched.web import appwiring

    source = Path(appwiring.__file__).read_text(encoding="utf-8")
    assert "postdeploy_watch.after_boot(server)" in source
    assert "postdeploy_task," in source, "cancelled in the shutdown half"
    assert 'if with_scheduler and not os.environ.get("RSCHED_NO_SCHEDULER")' in source
