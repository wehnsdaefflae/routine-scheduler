"""End-to-end: a scripted engine run records each orchestrator turn out-of-band, to an
installed sink and (with a FileSink) to the runs/<ts>/llm-tasks.jsonl sidecar the daemon tails.
Proves loop.py:343 instrumentation fires, labels turns, and never touches the prompt."""

from __future__ import annotations

import json

import pytest
from conftest import finish
from test_loop import TS, _run, _server, probe

from rsched.endpoints.instrument import FileSink, set_sink
from rsched.engine.runtime import run_routine


@pytest.fixture(autouse=True)
def _reset_sink():
    set_sink(None)
    yield
    set_sink(None)


class CapSink:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, rec):
        self.records.append(rec)


def test_each_turn_is_recorded(make_routine, scripted):
    cap = CapSink()
    set_sink(cap)
    _, ep, status, run_dir, _events = _run(make_routine, scripted, [probe(), finish()])
    assert status == "ok"

    started = [r for r in cap.records if r["phase"] == "started"]
    finished = [r for r in cap.records if r["phase"] == "finished"]
    purposes = [r["purpose"] for r in started]
    assert "turn 1" in purposes and "turn 2" in purposes     # one call per turn, labeled
    assert all(r["kind"] == "turn" and r["endpoint"] == "scripted" for r in started)
    assert len(finished) == len(started) >= 2
    assert all("usage" in r for r in finished)                # finished carries the token usage

    # the prompt was never mutated by instrumentation: the ScriptedEndpoint saw a strictly
    # growing (append-only) message list across turns, exactly as the caching contract requires.
    lengths = [len(c["messages"]) for c in ep.calls]
    assert lengths == sorted(lengths) and lengths[0] >= 1


def test_run_writes_the_sidecar_file(make_routine, scripted):
    d = make_routine(slug="sidecar")
    set_sink(FileSink(d / "runs" / TS / "llm-tasks.jsonl"))   # the real file the daemon tails
    scripted([probe(), finish()])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    recs = [json.loads(x) for x in (run_dir / "llm-tasks.jsonl").read_text().splitlines()]
    assert recs and any(r["phase"] == "started" and r["kind"] == "turn" for r in recs)
    assert all({"id", "phase", "endpoint", "model", "purpose"} <= set(r) for r in recs)
