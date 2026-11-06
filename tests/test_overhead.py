"""The per-run task/process/friction classifier (engine/overhead.py): every landed action in one
bucket, read from the transcript as the engine writes it — plain or rotated — so the same
function measures a live run and a kept one."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from rsched.engine import overhead
from rsched.engine.overhead import FRICTION, PROCESS, TASK, classify

ROOT = "/home/mark/routines/r"


def _act(kind: str, **fields) -> dict:
    return {"say": "x", "kind": kind, **fields}


@pytest.mark.parametrize(("action", "bucket"), [
    # the run working on itself, by kind
    (_act("memory_write", name="n"), PROCESS),
    (_act("memory_read", name="n"), PROCESS),
    (_act("read_rule", slug="web-research"), PROCESS),
    (_act("goal", verb="set"), PROCESS),
    (_act("job", verb="checkpoint"), PROCESS),
    # its recipe — the routine's own, and a JOB's inside its workspace
    (_act("read_file", paths=["stages/orient.md", "main.md"]), PROCESS),
    (_act("read_file", path="tasks/ards/stages/send.md"), PROCESS),
    (_act("edit_file", path=f"{ROOT}/LEDGER.md"), PROCESS),
    (_act("write_file", path="state/phase.json"), PROCESS),
    (_act("read_file", path="state/backlog.md"), PROCESS),
    (_act("write_file", path="state/mail-log.jsonl"), PROCESS),
    # product data under state/ is the task's when a file action touches it
    (_act("write_file", path="state/page_payload.json"), TASK),
    (_act("read_file", paths=["stages/orient.md", "state/shows.json"]), TASK),
    (_act("write_file", path="artifacts/report.html"), TASK),
    # outside the own dir only a file NAMED as the run's record is process
    (_act("edit_file", path="/srv/project/journal/WORKLOG.md"), PROCESS),
    (_act("edit_file", path="/srv/project/CHANGELOG.md"), TASK),
    (_act("read_file", path="/srv/project/src/app.py"), TASK),
    # calls, conservatively
    (_act("script", name="outcome", args=["--json"]), PROCESS),
    (_act("script", name="project_run", args=["bin/ledger.py", "set", "asks", "x=1"]), PROCESS),
    (_act("script", name="build", args=["state/page.json"]), TASK),
    (_act("util", name="code-search", args=["^##", "state/backlog.md"]), PROCESS),
    (_act("util", name="mail", args=["list", "--json"]), TASK),
    (_act("shell", command="cat state/phase.json; head -5 LEDGER.md"), PROCESS),
    (_act("shell", command="python3 -c \"import json; d=json.load(open('state/shows.json'))\""),
     PROCESS),                                # the one-off over nothing but its own state
    (_act("shell", command="cat state/shows.json /srv/videos/list.txt"), TASK),
    (_act("shell", command="pytest -q"), TASK),
    # a report or a question may BE the product: never process by default
    (_act("report", title="t"), TASK),
    (_act("ask_user", question="q"), TASK),
    (_act("finish", status="ok"), PROCESS),
])
def test_each_landed_action_lands_in_one_bucket(action, bucket):
    assert classify(action, {"kind": action["kind"]}, ROOT) == bucket


@pytest.mark.parametrize("obs", [
    {"kind": "reminder_hold"},                                 # held, not executed
    {"kind": "finish", "rejected": True},                      # a deferred finish
    {"kind": "memory_write", "error": "notes are capped at 100"},
    {"kind": "script", "exit": 2},                             # a tool call that failed
])
def test_friction_wins_whatever_the_action_was_aimed_at(obs):
    kind = "memory_write" if obs["kind"] == "reminder_hold" else obs["kind"]
    assert classify(_act(kind, name="outcome"), obs, ROOT) == FRICTION


def test_a_shell_that_exits_non_zero_is_grep_saying_no_match():
    assert classify(_act("shell", command="grep x /srv/a.txt"), {"exit": 1}, ROOT) == TASK


def _events() -> list[dict]:
    def action(kind, **f):
        return {"type": "assistant_action", "payload": _act(kind, **f)}

    def obs(kind, **f):
        return {"type": "observation", "payload": {"kind": kind, **f}}

    return [
        {"type": "header", "run_id": "r:1"},
        action("read_file", path="stages/a.md"), obs("read_file"),
        action("util", name="mail", args=["send"]), obs("util", exit=0),
        action("util", name="web", args=["get"]), obs("util", exit=0,
                                                      not_executed=[{"line": "x"}]),
        {"type": "error", "payload": {"where": "schema", "attempt": 1}},
        action("write_file", path="out.html"), obs("write_file"),
        action("finish", status="ok"),            # the finish leaves no observation
    ]


def test_a_run_counts_schema_rejections_and_unexecuted_chain_elements_as_friction():
    got = overhead.from_events(_events(), ROOT)
    assert got == {"task": 3, "process": 2, "friction": 2, "process_share": round(4 / 7, 3)}


def test_a_run_with_no_landed_action_is_unknown_not_zero(tmp_path):
    assert overhead.from_events([{"type": "header"}], ROOT) is None
    assert overhead.of_run(tmp_path / "gone", ROOT) is None


@pytest.mark.parametrize("rotated", [False, True])
def test_a_kept_transcript_reads_the_same_plain_or_rotated(tmp_path: Path, rotated):
    body = "".join(json.dumps(e) + "\n" for e in _events()).encode()
    if rotated:
        with gzip.open(tmp_path / "transcript.jsonl.gz", "wb") as fh:
            fh.write(body)
    else:
        (tmp_path / "transcript.jsonl").write_bytes(body + b'{"torn')
    assert overhead.of_run(tmp_path, ROOT)["process_share"] == round(4 / 7, 3)


def test_the_bar_sits_in_the_measured_gap():
    """The module docstring's calibration: the highest healthy median 0.263, the lowest flagged
    0.277 — a bar outside that gap would re-label a routine the measurement already judged."""
    assert 0.263 < overhead.THRESHOLD <= 0.277
    assert overhead.MIN_RUNS <= overhead.WINDOW
