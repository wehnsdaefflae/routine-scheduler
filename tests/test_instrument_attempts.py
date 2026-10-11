"""F655 end to end: a RETRIED call is visible where the call is visible.

The chain under test is the whole one the operator's 11-minute silence broke — an adapter that
uses the shared `with_retries` loop, wrapped by `InstrumentedEndpoint`, recording to a sink and
folded by the `TaskCenter` the dock mirrors. The record is on the SAME call id, because a retry
is the same call: one `started` row used to cover up to three attempts of 600 s, and nothing
anywhere said which attempt was running or what had ended the one before.
"""

from __future__ import annotations

import json

import pytest

from rsched.endpoints.base import Completion, EndpointError, with_retries
from rsched.endpoints.instrument import FileSink, InstrumentedEndpoint, set_sink
from rsched.llm_tasks import TaskCenter


class RetryingEndpoint:
    """An adapter shaped like the real ones: its own retry loop INSIDE complete()."""

    def __init__(self, failures: int, *, error: EndpointError | None = None):
        self.name = "proxy"
        self.failures = failures
        self.error = error or EndpointError(
            "proxy: read timeout after 600s (connected, but no reply within the bound)",
            retryable=True)
        self.attempts = 0

    def complete(self, messages, *, model, schema=None, effort=None, max_tokens=None,
                 timeout=600, temperature=None, cacheable=True):
        def call():
            self.attempts += 1
            if self.attempts <= self.failures:
                raise self.error
            return Completion(text="ok", usage={"in": 1, "out": 1}, provider="acme")

        return with_retries(call)


class CapturingSink:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, rec: dict) -> None:
        self.records.append(rec)


class FakeBus:
    def __init__(self):
        self.events: list[dict] = []

    def publish(self, ev: dict) -> None:
        self.events.append(ev)


pytestmark = pytest.mark.usefixtures("reset_llm_sink")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_a_retried_call_records_each_attempt_on_the_same_id():
    sink = CapturingSink()
    set_sink(sink)
    ep = RetryingEndpoint(failures=2)
    ep_wrapped = InstrumentedEndpoint(ep)
    ep_wrapped.complete([], model="m", purpose="turn 7", kind="turn")

    assert ep.attempts == 3
    assert [r["phase"] for r in sink.records] == ["started", "attempt", "attempt", "finished"]
    assert len({r["id"] for r in sink.records}) == 1, "a retry is the same call, not a new one"
    tries = [r for r in sink.records if r["phase"] == "attempt"]
    # the attempt NOW RUNNING is what a reader wants named, with the cause of the last one
    assert [r["attempt"] for r in tries] == [2, 3]
    assert all(r["tries"] == 3 for r in tries)
    assert all("read timeout after 600s" in r["after"] for r in tries)
    assert all(r["retry_in"] > 0 for r in tries), "the wait before the next attempt is recorded"
    # the descriptive fields ride every phase, as they do for started/finished
    assert all(r["purpose"] == "turn 7" and r["endpoint"] == "proxy" and r["model"] == "m"
               for r in sink.records)


def test_a_call_that_never_retries_records_nothing_extra():
    sink = CapturingSink()
    set_sink(sink)
    InstrumentedEndpoint(RetryingEndpoint(failures=0)).complete([], model="m", purpose="turn 1")
    assert [r["phase"] for r in sink.records] == ["started", "finished"]


def test_an_exhausted_call_books_each_boundary_once_and_the_failure_once():
    sink = CapturingSink()
    set_sink(sink)
    with pytest.raises(EndpointError):
        InstrumentedEndpoint(RetryingEndpoint(failures=9)).complete([], model="m", purpose="t")
    assert [r["phase"] for r in sink.records] == ["started", "attempt", "attempt", "failed"]
    assert [r["attempt"] for r in sink.records if r["phase"] == "attempt"] == [2, 3]


def test_the_sidecar_the_daemon_tails_carries_the_attempts(tmp_path):
    """The engine's own sink is a file the daemon tails, so the attempt has to survive JSONL."""
    path = tmp_path / "llm-tasks.jsonl"
    sink = FileSink(path)
    set_sink(sink)
    InstrumentedEndpoint(RetryingEndpoint(failures=1)).complete([], model="m", purpose="turn 3")
    sink.close()
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["phase"] for r in recs] == ["started", "attempt", "finished"]
    assert recs[1]["attempt"] == 2 and recs[1]["tries"] == 3 and recs[1]["after"]


def test_the_task_center_shows_a_retrying_call_as_in_flight_not_as_done():
    """The fold the dock mirrors: `attempt` is a THIRD state, neither thinking nor terminated.
    Reading it as unknown would have left the status at `running` (indistinguishable from a
    healthy call); reading it as terminal would have set `done_at` and pruned a live call.
    """
    bus = FakeBus()
    tc = TaskCenter(bus)
    base = {"id": "t1", "endpoint": "e", "model": "m", "purpose": "turn 4"}
    tc.ingest({**base, "phase": "started"})
    tc.ingest({**base, "phase": "attempt", "attempt": 2, "tries": 3,
               "after": "proxy: read timeout after 600s", "retry_in": 1.0})
    task = tc.snapshot()["tasks"][0]
    assert task["status"] == "retrying"
    assert task["attempt"] == 2 and task["tries"] == 3 and "600s" in task["after"]
    assert "done_at" not in task, "a retrying call must not look terminated"
    assert bus.events[-1]["status"] == "retrying"

    tc.ingest({**base, "phase": "finished", "usage": {"in": 1, "out": 2}})
    assert tc.snapshot()["tasks"][0]["status"] == "done"


def test_a_retrying_task_still_ages_out_and_still_counts_as_live(monkeypatch):
    """F572's leak was a predicate that named one in-flight status and missed another. A
    retrying call must be covered by BOTH halves: the age backstop that terminates an
    abandoned task, and the liveness check that keeps its process from pruning under it.
    """
    import rsched.llm_tasks as m

    bus = FakeBus()
    tc = TaskCenter(bus)
    tc.open_process("p1", kind="run", label="a run")
    tc.ingest({"id": "t1", "phase": "attempt", "attempt": 2, "tries": 3, "after": "boom",
               "endpoint": "e", "model": "m", "purpose": "turn 1", "process_id": "p1"})
    assert tc.snapshot()["tasks"][0]["status"] == "retrying"

    # its process closes while it is still retrying: the child is orphaned, not left live
    tc.close_process("p1")
    assert tc.snapshot()["tasks"][0]["status"] == "error"

    # and the age backstop reaches a retrying task that nothing ever closes
    tc2 = TaskCenter(FakeBus())
    tc2.ingest({"id": "t2", "phase": "attempt", "attempt": 2, "tries": 3, "after": "boom",
                "endpoint": "e", "model": "m", "purpose": "turn 1"})
    monkeypatch.setattr(m.time, "monotonic",
                        lambda: tc2.tasks["t2"]["started_at"] + m.MAX_RUNNING_S + 1)
    snap = tc2.snapshot()["tasks"]
    assert snap and snap[0]["status"] == "error" and "abandoned" in snap[0]["error"]
