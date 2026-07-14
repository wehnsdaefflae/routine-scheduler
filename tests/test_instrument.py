"""InstrumentedEndpoint + sinks: the seam every LLM call flows through must be transparent
(same Completion, same exceptions, all kwargs forwarded) and record started/finished/failed
out-of-band."""

from __future__ import annotations

import json
import threading

import pytest

from rsched.endpoints.base import Completion, EndpointError
from rsched.endpoints.instrument import (FileSink, InstrumentedEndpoint, current_process,
                                         make_record, process_scope, set_sink)


class StubEndpoint:
    """Minimal ChatEndpoint: records the kwargs it was called with, returns a fixed reply."""

    def __init__(self, *, reply: Completion | None = None, boom: Exception | None = None):
        self.name = "stub"
        self.context_chars = 123_000
        self.flavor = "vanilla"  # an adapter-specific attribute (tests __getattr__)
        self.calls: list[dict] = []
        self._reply = reply or Completion(text="ok", usage={"in": 7, "out": 3}, provider="acme")
        self._boom = boom

    def complete(self, messages, *, model, schema=None, effort=None, max_tokens=None,
                 timeout=600, session=None):
        self.calls.append(dict(messages=messages, model=model, schema=schema, effort=effort,
                               max_tokens=max_tokens, timeout=timeout, session=session))
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
                      effort="high", max_tokens=42, timeout=90, session="sess")
    assert out is stub._reply  # exact same object, unchanged
    # every standard kwarg forwarded verbatim; instrumentation kwargs never reach the adapter
    assert stub.calls == [dict(messages=[{"role": "user", "content": "hi"}], model="m",
                               schema={"x": 1}, effort="high", max_tokens=42, timeout=90,
                               session="sess")]


def test_proxies_name_context_and_adapter_attrs():
    ep = InstrumentedEndpoint(StubEndpoint())
    assert ep.name == "stub"
    assert ep.context_chars == 123_000
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
    InstrumentedEndpoint(stub).complete([], model="m", purpose="p", process="proc-1", kind="k")
    assert "purpose" not in stub.calls[0] and "process" not in stub.calls[0]
    assert set(stub.calls[0]) == {"messages", "model", "schema", "effort", "max_tokens",
                                  "timeout", "session"}


def test_exception_emits_failed_and_reraises():
    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint(boom=EndpointError("nope", retryable=False)))
    with pytest.raises(EndpointError, match="nope"):
        ep.complete([], model="m", purpose="Draft workflow")
    assert [r["phase"] for r in sink.records] == ["started", "failed"]
    assert sink.records[1]["error"] == "nope"
    assert sink.records[0]["id"] == sink.records[1]["id"]


def test_process_scope_attributes_records():
    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint())
    assert current_process() is None
    with process_scope("create:abc"):
        ep.complete([], model="m", purpose="Decompose")
    assert current_process() is None  # restored on exit
    assert sink.records[0]["process_id"] == "create:abc"
    # an explicit process= arg overrides the ambient scope
    ep.complete([], model="m", purpose="x", process="explicit")
    assert sink.records[-1]["process_id"] == "explicit"


def test_sink_failure_never_breaks_the_call():
    class BoomSink:  # a sink whose record() always raises
        def record(self, rec):
            raise RuntimeError("sink down")

    set_sink(BoomSink())
    out = InstrumentedEndpoint(StubEndpoint()).complete([], model="m", purpose="p")
    assert out.text == "ok"  # the real call still returns


def test_process_scope_propagates_across_to_thread():
    import asyncio

    sink = CapturingSink()
    set_sink(sink)
    ep = InstrumentedEndpoint(StubEndpoint())

    async def driver():
        with process_scope("wiz-1"):
            # workflow calls run under asyncio.to_thread — the context must copy into the worker
            await asyncio.to_thread(ep.complete, [], model="m", purpose="suggest tags")

    asyncio.run(driver())
    assert sink.records[0]["process_id"] == "wiz-1"


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
