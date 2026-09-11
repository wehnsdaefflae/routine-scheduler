"""The prompt-cache read share: the one reading that separates a healthy append-only run
from one re-writing its prefix every turn, and the health event that surfaces it.

The failure this guards is SILENT by construction — reads stay non-zero (the static
system+tools prefix keeps hitting), the run's token count falls, and the extra cost lands
as a subscription weighting rather than a bill. Only reads ÷ writes shows it.
"""

from __future__ import annotations

import json

from conftest import finish
from rsched.endpoints.base import Completion, cache_read_share
from rsched.engine.runtime import run_routine
from test_loop import TS, _server, probe


def _events(server) -> list[dict]:
    path = server.routines_home / ".control" / "health-events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_share_is_none_when_the_transport_reports_no_cache():
    """Absence is not a low share: an endpoint that never caches must not read as broken."""
    assert cache_read_share({"in": 5_000, "out": 200}) is None
    assert cache_read_share(None) is None
    assert cache_read_share({}) is None


def test_share_is_reads_over_all_cache_traffic():
    assert cache_read_share({"cached_in": 90, "cache_write": 10}) == 0.9
    assert cache_read_share({"cached_in": 0, "cache_write": 10}) == 0.0
    # the broken shape: reads PINNED at the static prefix while the conversation re-writes
    assert cache_read_share({"cached_in": 35_346, "cache_write": 209_422}) < 0.2


def _run_with_usage(make_routine, scripted, usage: dict):
    d = make_routine(slug="cachey")
    reply = Completion(text=json.dumps(probe()), parsed=probe(), usage=usage)
    done = Completion(text=json.dumps(finish()), parsed=finish(), usage=usage)
    scripted([reply, done])
    server = _server(d)
    status, _ = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    return server


def test_degraded_share_raises_a_health_event(make_routine, scripted):
    server = _run_with_usage(make_routine, scripted,
                             {"in": 10, "out": 5, "cached_in": 20_000, "cache_write": 300_000})

    got = [e for e in _events(server) if e["event"] == "cache_read_degraded"]
    assert len(got) == 1
    assert got[0]["routine"] == "cachey"
    # structured fields, not prose: "is any transport doing this, and since when" has to be
    # answerable by a filter over the log
    assert got[0]["cache_read_share"] < 0.5
    assert got[0]["cache_write_tokens"] == 600_000     # both turns folded
    assert "re-written" in got[0]["detail"]


def test_healthy_share_is_silent(make_routine, scripted):
    server = _run_with_usage(make_routine, scripted,
                             {"in": 10, "out": 5, "cached_in": 400_000, "cache_write": 10_000})
    assert [e for e in _events(server) if e["event"] == "cache_read_degraded"] == []


def test_a_run_too_small_to_judge_is_silent(make_routine, scripted):
    """A short run whose prefix was written once and read twice is not a regression — the
    ratio only means something once there is enough traffic behind it."""
    server = _run_with_usage(make_routine, scripted,
                             {"in": 10, "out": 5, "cached_in": 100, "cache_write": 4_000})
    assert [e for e in _events(server) if e["event"] == "cache_read_degraded"] == []


def test_an_uncached_transport_is_silent(make_routine, scripted):
    server = _run_with_usage(make_routine, scripted, {"in": 50_000, "out": 500})
    assert [e for e in _events(server) if e["event"] == "cache_read_degraded"] == []
