"""InstrumentedEndpoint + sinks: the seam every LLM call flows through must be transparent
(same Completion, same exceptions, all kwargs forwarded) and record started/finished/failed
out-of-band."""

from __future__ import annotations

import json
import threading

import pytest

from rsched.endpoints.base import Completion, EndpointError
from rsched.endpoints.instrument import (
    FileSink,
    InstrumentedEndpoint,
    abandon_open_calls,
    make_record,
    note_started,
    set_sink,
)


class StubEndpoint:
    """Minimal ChatEndpoint: records the kwargs it was called with, returns a fixed reply."""

    def __init__(self, *, reply: Completion | None = None, boom: Exception | None = None):
        self.name = "stub"
        self.context_tokens = 123_000
        self.flavor = "vanilla"  # an adapter-specific attribute (tests __getattr__)
        self.calls: list[dict] = []
        self._reply = reply or Completion(text="ok", usage={"in": 7, "out": 3}, provider="acme")
        self._boom = boom

    def complete(self, messages, *, model, schema=None, effort=None, max_tokens=None,
                 timeout=600, temperature=None, cacheable=True):
        self.calls.append({"messages": messages, "model": model, "schema": schema,
                           "effort": effort, "max_tokens": max_tokens, "timeout": timeout,
                           "temperature": temperature, "cacheable": cacheable})
        if self._boom is not None:
            raise self._boom
        return self._reply


class CapturingSink:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, rec: dict) -> None:
        self.records.append(rec)


@pytest.fixture(autouse=True)
def _reset_sink():
    set_sink(None)
    yield
    set_sink(None)


def test_passthrough_when_no_sink():
    stub = StubEndpoint()
    ep = InstrumentedEndpoint(stub)
    out = ep.complete([{"role": "user", "content": "hi"}], model="m", schema={"x": 1},
                      effort="high", max_tokens=42, timeout=90)
    assert out is stub._reply  # exact same object, unchanged
    # every standard kwarg forwarded verbatim; instrumentation kwargs never reach the adapter
    # — except `cacheable`, which is DERIVED from the kind here (see test_only_a_turn_is_cacheable)
    assert stub.calls == [{"messages": [{"role": "user", "content": "hi"}], "model": "m",
                           "schema": {"x": 1}, "effort": "high", "max_tokens": 42,
                           "timeout": 90, "temperature": None, "cacheable": False}]


def test_proxies_name_context_and_adapter_attrs():
    ep = InstrumentedEndpoint(StubEndpoint())
    assert ep.name == "stub"
    assert ep.context_tokens == 123_000
    assert ep.flavor == "vanilla"  # __getattr__ fallthrough


def test_records_started_and_finished():
    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint())
    ep.complete([{"role": "user", "content": "hi"}], model="m-1", purpose="Rank workflows",
                kind="suggest")
    assert [r["phase"] for r in sink.records] == ["started", "finished"]
    started, finished = sink.records
    assert started["id"] == finished["id"]  # one task id across its lifecycle
    assert started["endpoint"] == "stub" and started["model"] == "m-1"
    assert started["purpose"] == "Rank workflows" and started["kind"] == "suggest"
    assert finished["usage"] == {"in": 7, "out": 3} and finished["provider"] == "acme"
    assert "purpose" in finished  # descriptive fields ride every phase


def test_purpose_and_kind_not_forwarded_to_adapter():
    stub = StubEndpoint()
    set_sink(CapturingSink())
    InstrumentedEndpoint(stub).complete([], model="m", purpose="p", kind="k")
    assert "purpose" not in stub.calls[0] and "process" not in stub.calls[0]
    assert set(stub.calls[0]) == {"messages", "model", "schema", "effort", "max_tokens",
                                  "timeout", "temperature", "cacheable"}


def test_only_a_turn_is_cacheable():
    """A cache breakpoint pays only where the prefix is sent AGAIN. This wrapper is the one
    seam every completion passes through and it already knows the kind, so the decision is
    derived here rather than at ten call sites that could forget it. Measured 2026-09-11:
    0.3% read share on `llm_action` against 96% on turns — the write was pure surcharge.
    """
    for kind, want in (("turn", True), ("llm_action", False), ("compaction", False),
                       ("autolabel", False), (None, False)):
        stub = StubEndpoint()
        set_sink(None)                     # the fast path forwards the same kwargs
        InstrumentedEndpoint(stub).complete([], model="m", kind=kind)
        assert stub.calls[0]["cacheable"] is want, kind
        set_sink(CapturingSink())          # …and so does the observed path
        stub2 = StubEndpoint()
        InstrumentedEndpoint(stub2).complete([], model="m", kind=kind)
        assert stub2.calls[0]["cacheable"] is want, kind


def test_exception_emits_failed_and_reraises():
    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint(boom=EndpointError("nope", retryable=False)))
    with pytest.raises(EndpointError, match="nope"):
        ep.complete([], model="m", purpose="Draft workflow")
    assert [r["phase"] for r in sink.records] == ["started", "failed"]
    assert sink.records[1]["error"] == "nope"
    assert sink.records[0]["id"] == sink.records[1]["id"]
def test_sink_failure_never_breaks_the_call():
    class BoomSink:  # a sink whose record() always raises
        def record(self, rec):
            raise RuntimeError("sink down")

    set_sink(BoomSink())
    out = InstrumentedEndpoint(StubEndpoint()).complete([], model="m", purpose="p")
    assert out.text == "ok"  # the real call still returns


def test_filesink_writes_valid_jsonl(tmp_path):
    sink = FileSink(tmp_path / "sub" / "llm-tasks.jsonl")  # parent created lazily
    sink.record(make_record("started", id="a", endpoint="e", model="m", purpose="p"))
    sink.record(make_record("finished", id="a", endpoint="e", model="m", purpose="p",
                            usage={"in": 1, "out": 2}))
    sink.close()
    lines = (tmp_path / "sub" / "llm-tasks.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["phase"] for x in lines] == ["started", "finished"]


def test_filesink_thread_safe_appends(tmp_path):
    sink = FileSink(tmp_path / "llm-tasks.jsonl")

    def worker(n):
        for i in range(20):
            sink.record(make_record("started", id=f"{n}-{i}", endpoint="e", model="m", purpose="p"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sink.close()
    lines = (tmp_path / "llm-tasks.jsonl").read_text().splitlines()
    assert len(lines) == 100
    assert all(json.loads(x)["id"] for x in lines)  # every line is complete, parseable JSON


# --- D157-C part B: an abandoned call must terminate its OWN record ---------------------------
# F572, measured from the real sidecar of conversation c-20260915-074755 (884 call ids): 23 ids
# carry a `started` record and no terminal one, and 17 of those 23 are `Compaction · archival`.
# The wrapper's `except BaseException` does not cover them because archival runs in a DAEMON
# thread (engine/archival.py:start) that the interpreter kills at process exit without unwinding
# its stack — so the `failed` record is never written, the task center never sets `done_at`, and
# `_prune` can never drop the task. `settle()` already KNOWS it is abandoning the archive; it
# just had no way to say so, because the call id is minted inside complete() and never returned.

def test_abandon_open_calls_terminates_only_unfinished_records():
    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint())
    ep.complete([{"role": "user", "content": "x"}], model="m", purpose="Compaction · archival",
                kind="compaction")
    assert [r["phase"] for r in sink.records] == ["started", "finished"]
    n = abandon_open_calls(purpose="Compaction · archival", error="the run ended")
    assert n == 0, "a call that already reported back must not be re-terminated"
    assert [r["phase"] for r in sink.records] == ["started", "finished"]


def test_abandon_open_calls_writes_failed_for_a_started_call():
    """The archival case: a `started` record whose call never returns."""
    sink = CapturingSink()
    set_sink(sink)
    # emit a `started` with no terminal phase, exactly as a killed daemon thread leaves behind
    rec = make_record("started", id="abc123", endpoint="e", model="m",
                      purpose="Compaction · archival", kind="compaction")
    sink.record(rec)
    note_started(rec)
    n = abandon_open_calls(purpose="Compaction · archival",
                          error="the run ended before the archive finished")
    assert n == 1
    last = sink.records[-1]
    assert last["phase"] == "failed" and last["id"] == "abc123"
    assert last["error"] == "the run ended before the archive finished"
    assert last["purpose"] == "Compaction · archival", "the record stays self-describing"
    # idempotent: abandoning twice must not write a second failed record
    assert abandon_open_calls(purpose="Compaction · archival", error="again") == 0


def test_abandon_open_calls_filters_by_purpose():
    sink = CapturingSink()
    set_sink(sink)
    for cid, purpose in (("a1", "Compaction · archival"), ("b2", "turn 42")):
        rec = make_record("started", id=cid, endpoint="e", model="m", purpose=purpose)
        sink.record(rec)
        note_started(rec)
    assert abandon_open_calls(purpose="Compaction · archival", error="gone") == 1
    assert sink.records[-1]["id"] == "a1"
    assert abandon_open_calls(error="everything") == 1, "no purpose = every open call"
    assert sink.records[-1]["id"] == "b2"


def test_abandon_open_calls_is_safe_with_no_sink():
    set_sink(None)
    assert abandon_open_calls(purpose="anything", error="e") == 0
