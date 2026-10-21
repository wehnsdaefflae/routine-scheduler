"""F577: a UI trace row's `ts` is when the EVENT happened, not when its BATCH was flushed.

Before this, `static/trace.js` sent no timestamp, `TraceEvent` had no `ts` field to carry one
and `api_traces._append` computed a single `now` for a whole POST — so every row of a flush
window (5 s, or a whole session tail on `pagehide`) shared one instant. Inter-event timing is
the one quantity the store did not preserve, and it is the only quantity the friction
inference needs: three consumers read it (self-audit's evidence pass, its
`scripts/ui_trace_audit.py`, the routine-improver's improve-ui lens) and none of them could
tell 3 clicks in 3 seconds from 3 clicks in 3 minutes. Worse, it manufactured co-occurrence
between events that merely shared a flush.

What is pinned here: the client's own event time survives the batch when it is credible; the
server's receive stamp stays beside it under `received`, so a skewed client clock is
measurable instead of silently trusted; and `ts_src` names which clock produced `ts`, so a
fallback is READ, never inferred. Old rows on disk (no `received`, no `ts_src`) are untouched
— every reader takes these fields with `.get`.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import authed_client, make_test_server
from rsched.web.api_traces import MAX_TS_LAG_S, MAX_TS_LEAD_S, record_server_trace


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _rows(tmp) -> list[dict]:
    tdir = tmp / "routines" / ".ui-traces"
    return [json.loads(ln) for p in sorted(tdir.glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _iso(offset_s: float) -> str:
    """A client stamp `offset_s` from now — negative is the ordinary case (queued, then flushed)."""
    return (dt.datetime.now(dt.UTC)
            + dt.timedelta(seconds=offset_s)).isoformat(timespec="milliseconds")


def test_a_batch_keeps_the_interval_between_its_events(client):
    """The defect itself: two clicks 4 s apart arriving in ONE flush stay 4 s apart."""
    c, tmp = client
    first, second = _iso(-4.5), _iso(-0.5)
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "click", "view": "questions", "target": "dismiss all 2", "ts": first},
        {"kind": "click", "view": "questions", "target": "dismiss all 2", "ts": second},
    ]})
    assert r.status_code == 200 and r.json()["recorded"] == 2
    rows = _rows(tmp)
    assert [x["ts_src"] for x in rows] == ["client", "client"]
    gap = (dt.datetime.fromisoformat(rows[1]["ts"])
           - dt.datetime.fromisoformat(rows[0]["ts"])).total_seconds()
    assert 3.5 < gap < 4.5, rows                      # the 4 s the user spent, not 0
    # ONE batch: the server stamp is shared, and it is the thing `ts` used to be
    assert rows[0]["received"] == rows[1]["received"]
    assert rows[0]["ts"] != rows[0]["received"]


@pytest.mark.parametrize("offset_s", [-(MAX_TS_LAG_S - 5), -30, -5, -1, 0, 1, MAX_TS_LEAD_S - 1])
def test_a_credible_client_stamp_is_kept(client, offset_s):
    """A clock a second off — and the ordinary queue lag up to the lateness bound — is trusted."""
    c, tmp = client
    sent = _iso(offset_s)
    c.post("/api/ui-trace", json={"events": [{"kind": "nav", "view": "run", "ts": sent}]})
    (row,) = _rows(tmp)
    assert row["ts_src"] == "client"
    assert dt.datetime.fromisoformat(row["ts"]) == dt.datetime.fromisoformat(sent)


# A stamp this test sends, either as an OFFSET from now (built inside the test) or as a literal.
# The offsets stay numbers here on purpose: an absolute time computed in the decorator is
# computed at IMPORT, and under xdist each worker imports at its own millisecond — which makes
# the parametrize ids differ per worker and collection disagree ("Different tests were collected
# between gw1 and gw9"). A test id must be a constant.
IMPLAUSIBLE = [
    -86400,                  # a day stale: a queue cannot hold that, a clock can
    -(MAX_TS_LAG_S + 60),    # past the lateness bound
    MAX_TS_LEAD_S + 60,      # minutes in the FUTURE — nothing legitimate does this
    "not-a-time",            # unparseable
    "2026-10-21T07:12:27",   # naive: no instant at all, just a wall clock somewhere
    "x" * 40,                # over the ISO length any real stamp needs, sliced then refused
]


@pytest.mark.parametrize("sent", IMPLAUSIBLE)
def test_an_implausible_client_stamp_falls_back_visibly(client, sent):
    """A refused stamp still leaves a USABLE row: the server's time, and the fact it fell back."""
    c, tmp = client
    ts = _iso(sent) if isinstance(sent, (int, float)) else sent
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "click", "view": "audit", "target": "run self-audit now", "ts": ts},
    ]})
    assert r.status_code == 200 and r.json()["recorded"] == 1
    (row,) = _rows(tmp)
    assert row["ts_src"] == "skew"                     # read, not inferred
    assert row["ts"] == row["received"]                # usable: the server's own stamp
    assert row["target"] == "run self-audit now"       # and the event itself is intact


def test_a_record_with_no_client_stamp_is_the_servers_own(client):
    """A client that sends nothing, and every server-originated trace, say `server`."""
    c, tmp = client
    c.post("/api/ui-trace", json={"events": [{"kind": "nav", "view": "log"}]})
    record_server_trace(c.app.state.server, kind="stream-close", target="uir:1", detail="end")
    rows = _rows(tmp)
    assert [x["ts_src"] for x in rows] == ["server", "server"]
    assert all(x["ts"] == x["received"] for x in rows), rows


def test_the_day_file_follows_the_server_clock(client):
    """A client stamp never chooses the file: `_prune` compares day-file stems to a server
    cutoff, so a wrong clock must not be able to write a file that is pruned at once (or never).
    """
    c, tmp = client
    c.post("/api/ui-trace", json={"events": [
        {"kind": "nav", "view": "run", "ts": _iso(-86400)},
        {"kind": "nav", "view": "run", "ts": _iso(-1)},
    ]})
    files = [p.name for p in (tmp / "routines" / ".ui-traces").glob("*.jsonl")]
    assert files == [f"{dt.datetime.now(dt.UTC).strftime('%Y%m%d')}.jsonl"]
