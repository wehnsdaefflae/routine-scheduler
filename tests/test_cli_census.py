"""`rsched census` counts what a tmp instance's transcripts, configs and health stream actually
use, per name and per distinct routine or conversation, and names every known entry at zero."""

import gzip
import json
import os
from datetime import UTC, datetime, timedelta

import yaml

from rsched import cli
from rsched.cli_census import census
from rsched.engine.actionschema import KINDS

NOW = datetime.now(tz=UTC)


def _action(kind, days_ago=0, **side):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return json.dumps({"ts": ts, "type": "assistant_action", "turn": 1,
                       "payload": {"say": "s", "kind": kind, **side}}) + "\n"


def _seed(server, make_routine):
    alpha, beta = make_routine(slug="alpha"), make_routine(slug="beta")
    raw = yaml.safe_load((alpha / "routine.yaml").read_text(encoding="utf-8"))
    raw["capabilities"] = {"actions": ["shell"], "reminders": "local"}
    (alpha / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = alpha / "runs" / "20261120-010000"
    run.mkdir(parents=True)
    (run / "transcript.jsonl").write_text(
        _action("read_file", note="kept") + _action("read_file") + _action("shell", days_ago=90)
        + '{"ts": "x", "type": "observation"}\nnot json\n', encoding="utf-8")
    (run / "sub" / "1").mkdir(parents=True)
    (run / "sub" / "1" / "transcript.jsonl").write_text(_action("finish"), encoding="utf-8")
    old = beta / "runs" / "20260101-010000"
    old.mkdir(parents=True)
    with gzip.open(old / "transcript.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(_action("read_file"))
    conv = server.conversations_home / "c-20261120-010000" / "runs" / "20261120-010000"
    conv.mkdir(parents=True)
    (conv / "transcript.jsonl").write_text(_action("create_routine"), encoding="utf-8")
    control = server.routines_home / ".control"
    control.mkdir()
    (control / "health-events.jsonl").write_text(
        json.dumps({"ts": NOW.isoformat(), "event": "run_failed", "routine": "alpha"}) + "\n"
        + json.dumps({"ts": "2020-01-01T00:00:00", "event": "run_partial", "routine": "beta"})
        + "\n", encoding="utf-8")
    return old / "transcript.jsonl.gz"


def _used(section):
    return {row["name"]: (row["count"], row["subjects"]) for row in section["used"]}


def test_census_counts_use_and_names_what_is_never_used(cli_server, make_routine):
    stale = _seed(cli_server, make_routine)
    doc = census(cli_server, 60)
    assert doc["transcripts"] == 4      # alpha's run and child, beta's gz, the conversation
    assert _used(doc["action_kinds"]) == {"read_file": (3, 2), "finish": (1, 1),
                                          "create_routine": (1, 1)}
    assert "shell" in doc["action_kinds"]["never"]            # its only use is past the window
    assert set(doc["action_kinds"]["never"]) == set(KINDS) - {"read_file", "finish",
                                                              "create_routine"}
    assert _used(doc["side_fields"]) == {"note": (1, 1)}
    assert "role_swap" in doc["side_fields"]["never"]
    assert _used(doc["config_keys"])["budgets"] == (2, 2)
    assert "capabilities" not in doc["config_keys"]["never"]
    assert _used(doc["capabilities"]) == {"actions=shell": (1, 1), "reminders=local": (1, 1)}
    assert "never" not in doc["capabilities"]
    assert _used(doc["health_events"]) == {"run_failed": (1, 1)}
    assert "run_partial" in doc["health_events"]["never"]
    old = (NOW - timedelta(days=120)).timestamp()
    os.utime(stale, (old, old))
    assert census(cli_server, 60)["transcripts"] == 3          # a file untouched since is skipped


def test_census_cli_prints_lines_or_one_json_document(cli_server, make_routine, capsys):
    _seed(cli_server, make_routine)
    assert cli.main(["census"]) == 0
    out = capsys.readouterr().out
    assert "action_kinds:" in out and "never used:" in out and "read_file" in out
    assert cli.main(["census", "--json", "--days", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["days"] == 30


def test_an_alias_kind_is_counted_from_its_mark_and_the_util_call_too(cli_server, make_routine):
    """A `websearch` spelling lands as `util`; the event's `spelled` mark is what the census counts
    under the alias kind, and the util count stays every util call however it was written."""
    run = make_routine(slug="alpha") / "runs" / "20261120-010000"
    run.mkdir(parents=True)
    marked = json.loads(_action("util", name="websearch", args=["q"]))
    (run / "transcript.jsonl").write_text(
        json.dumps({**marked, "spelled": "websearch"}) + "\n" + _action("util", name="x"),
        encoding="utf-8")
    kinds = census(cli_server, 60)["action_kinds"]
    assert _used(kinds) == {"util": (2, 1), "websearch": (1, 1)}
    assert "websearch" not in kinds["never"] and "code_search" in kinds["never"]


def test_config_keys_are_measured_in_every_home_and_runtime_fields_are_not_keys(cli_server,
                                                                                make_routine):
    """`kind`/`playbook` live in a conversation's routine.yaml and `owner` in a background task's,
    so the census reads those homes too; the loader-filled fields are not vocabulary at all."""
    make_routine(slug="alpha")
    for home, raw in ((cli_server.conversations_home / "c-1",
                       {"kind": "conversation", "playbook": {"slug": "p"}}),
                      (cli_server.background_home / "bg-1", {"owner": {"slug": "c-1"}})):
        home.mkdir(parents=True)
        (home / "routine.yaml").write_text(yaml.safe_dump({"slug": home.name, **raw}),
                                           encoding="utf-8")
    (cli_server.background_home / ".requests").mkdir()
    doc = census(cli_server, 60)
    used = _used(doc["config_keys"])
    assert doc["configs"] == 3
    assert used["slug"] == (3, 3)
    assert used["kind"] == used["playbook"] == used["owner"] == (1, 1)
    assert not {"kind", "playbook", "owner"} & set(doc["config_keys"]["never"])
    assert not {"dir", "deliberation", "tuning"} & set(doc["config_keys"]["never"])
