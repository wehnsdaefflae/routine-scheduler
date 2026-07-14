"""TaskCenter: the daemon's live registry of LLM work. Records fold into processes + tasks,
publish bus events, reconcile via snapshot(), and prune after a linger."""

from __future__ import annotations

import rsched.llm_tasks as m
from rsched.llm_tasks import DaemonSink, TaskCenter


class FakeBus:
    def __init__(self):
        self.events: list[dict] = []

    def publish(self, ev: dict) -> None:
        self.events.append(ev)


def _rec(id, phase, **extra):
    return {"id": id, "phase": phase, "endpoint": "e", "model": "m", "purpose": "p", **extra}


def test_ingest_tracks_status_and_publishes():
    bus = FakeBus()
    tc = TaskCenter(bus)
    tc.ingest(_rec("t1", "started"))
    tc.ingest(_rec("t1", "finished", usage={"in": 1, "out": 2}))
    assert [e["event"] for e in bus.events] == ["llm_task", "llm_task"]
    assert bus.events[0]["status"] == "running"
    assert bus.events[1]["status"] == "done"
    tasks = tc.snapshot()["tasks"]
    assert len(tasks) == 1 and tasks[0]["status"] == "done" and tasks[0]["usage"] == {"in": 1, "out": 2}


def test_failed_status():
    tc = TaskCenter(FakeBus())
    tc.ingest(_rec("t1", "started"))
    tc.ingest(_rec("t1", "failed", error="boom"))
    assert tc.snapshot()["tasks"][0]["status"] == "error"


def test_ingest_without_id_is_ignored():
    tc = TaskCenter(FakeBus())
    tc.ingest({"phase": "started"})
    assert tc.snapshot()["tasks"] == []


def test_open_close_process_and_snapshot():
    bus = FakeBus()
    tc = TaskCenter(bus)
    tc.open_process("create:abc", kind="wizard", label="Create routine: X")
    assert bus.events[0]["event"] == "llm_process" and bus.events[0]["phase"] == "opened"
    procs = tc.snapshot()["processes"]
    assert procs[0]["id"] == "create:abc" and procs[0]["closed"] is False
    assert "closed_at" not in procs[0]  # internal field stripped from the public snapshot
    tc.close_process("create:abc")
    assert bus.events[-1]["phase"] == "closed"


def test_terminal_task_pruned_after_linger(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    tc = TaskCenter(FakeBus())
    tc.ingest(_rec("t1", "finished"))
    assert len(tc.snapshot()["tasks"]) == 1
    clock[0] += m.LINGER_S + 1
    assert tc.snapshot()["tasks"] == []


def test_closed_process_removed_once_children_terminal(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    tc = TaskCenter(FakeBus())
    tc.open_process("p1", kind="wizard", label="L")
    tc.ingest(_rec("t1", "started", process_id="p1"))
    tc.close_process("p1")
    # a running child keeps the closed process visible
    assert any(p["id"] == "p1" for p in tc.snapshot()["processes"])
    tc.ingest(_rec("t1", "finished", process_id="p1"))
    clock[0] += m.LINGER_S + 1
    assert not any(p["id"] == "p1" for p in tc.snapshot()["processes"])


def test_close_process_orphans_in_flight_children(monkeypatch):
    # a run/subprocess killed mid-call leaves a `started` record with no `finished`; closing
    # its process must orphan that child so it (and the process) prune instead of leaking.
    clock = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    tc = TaskCenter(FakeBus())
    tc.open_process("p1", kind="run", label="r")
    tc.ingest(_rec("t1", "started", process_id="p1"))   # still in flight
    tc.close_process("p1")                               # e.g. aborted mid-call
    orphan = next(iter(tc.tasks.values()))
    assert orphan["status"] == "error" and orphan["error"]
    clock[0] += m.LINGER_S + 1
    snap = tc.snapshot()
    assert snap["tasks"] == [] and snap["processes"] == []   # both pruned, no leak


def test_task_cap_evicts_oldest_terminal(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    tc = TaskCenter(FakeBus())
    for i in range(m.MAX_TASKS + 50):
        clock[0] += 0.001  # keep them inside the linger window so time-pruning doesn't fire
        tc.ingest(_rec(f"t{i}", "finished"))
    assert len(tc.tasks) <= m.MAX_TASKS


def test_daemon_sink_marshals_onto_loop():
    invoked = []

    class FakeLoop:
        def call_soon_threadsafe(self, fn, *args):
            invoked.append((fn, args))
            fn(*args)  # emulate the loop running the scheduled callback

    tc = TaskCenter(FakeBus())
    DaemonSink(tc, FakeLoop()).record(_rec("t1", "started"))
    assert len(invoked) == 1
    assert tc.snapshot()["tasks"][0]["id"] == "t1"
