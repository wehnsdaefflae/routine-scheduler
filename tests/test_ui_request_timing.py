"""F858: the console records how long its own requests took, and how long the page took to load.

The UI trace store existed to answer "where is the console slow", and held no duration of any
kind. Measured 2026-11-08 over the whole 14-day retention window of
`routines_home/.ui-traces/`: 2,016 `click`, 819 `nav`, 654 `stream-close`, 178 `reconnect`, 22
`error` — and **zero** rows carrying a time any quantity could be read off.

Why the existing instruments did not cover it:

- `trace.js`'s freeze observer (F218) reads the browser's **Long Tasks** API, which fires only
  for work that BLOCKS THE MAIN THREAD. Time spent *waiting* on a request blocks nothing, so a
  page that feels slow because its requests are slow produced no event at all — which is why
  `freeze` has 0 rows in 14 days while the operator reports the console as slow.
- the server's own `slow_requests` deque (`web/app.py`) keeps the last 50 requests over 2.0 s
  **in RAM**, so a daemon restart erases it (one happened 2026-11-08T17:56), and
  `/api/debug/slow` is primary-token-only — no run may read it (403, `app.ROUTINE_TOKEN_DENIED_READS`).

So two kinds are added to the SAME store the other friction evidence lands in: `slow`, one row
per request over the tracer's floor, carrying the endpoint, the milliseconds, the status and the
concurrent stream count; and `load`, one row per page load carrying the navigation milestones.

What is pinned here: both kinds survive ingestion with their detail intact, the allowlist is
still an allowlist, the query string never reaches the store (it can carry what the operator
typed), and — the one that matters most — the client measures the FETCH alone and never the
token gate above it, which waits on a human and would book minutes of typing as server latency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import authed_client, make_test_server
from rsched.web.api_traces import KINDS

STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="timr")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _rows(tmp) -> list[dict]:
    tdir = tmp / "routines" / ".ui-traces"
    return [json.loads(ln) for p in sorted(tdir.glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_a_slow_request_is_stored_with_its_endpoint_and_milliseconds(client):
    """The row a latency question is answered from: which endpoint, how long, under what load."""
    c, tmp = client
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "slow", "view": "run", "target": "/api/runs/self-audit:1/transcript",
         "detail": "2400ms, status 200, 3 streams open"}]})
    assert r.status_code == 200 and r.json()["recorded"] == 1
    (row,) = _rows(tmp)
    assert row["kind"] == "slow"
    assert row["target"] == "/api/runs/self-audit:1/transcript"
    assert "2400ms" in row["detail"] and "3 streams open" in row["detail"]
    assert row["view"] == "run"           # which page was open when it was slow


def test_a_page_load_is_stored_with_its_navigation_milestones(client):
    """`load` is the other half: the cost of the page becoming usable at all, once per load."""
    c, tmp = client
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "load", "view": "dashboard", "target": "/",
         "detail": "response 120ms, dom 890ms, load 1640ms"}]})
    assert r.status_code == 200 and r.json()["recorded"] == 1
    (row,) = _rows(tmp)
    assert row["kind"] == "load"
    assert "dom 890ms" in row["detail"] and "load 1640ms" in row["detail"]


def test_both_new_kinds_are_on_the_allowlist_and_it_is_still_an_allowlist(client):
    """Widening the allowlist by two is not opening it: an unknown kind is still dropped."""
    assert "slow" in KINDS and "load" in KINDS
    c, tmp = client
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "timing", "view": "run", "target": "/api/x"},      # not a kind
        {"kind": "slow", "view": "run", "target": "/api/x", "detail": "1200ms, status 200"}]})
    assert r.json()["recorded"] == 1
    assert [x["kind"] for x in _rows(tmp)] == ["slow"]


# -- the client half, read from the shipped source ------------------------------------------


def _authed_fetch_body() -> str:
    """`authedFetch`'s own text — from its signature to the closing brace at column 0."""
    text = (STATIC / "api.js").read_text(encoding="utf-8")
    start = text.index("async function authedFetch(")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_the_fetch_is_measured_without_the_token_gate():
    """The one mistake this instrument must never make: timing the token prompt.

    `authedFetch` may pause on `requestToken` to ask the OPERATOR for a token — minutes of a
    human typing, on a phone with no devtools. A clock started before that wait books it as
    request latency and would make the slowest rows in the store the ones where somebody was
    signing in. So the start stamp has to be taken AFTER the gate and the observer called right
    after the fetch returns.
    """
    body = _authed_fetch_body()
    gate = body.index("await requestToken(")
    started = body.index("performance.now()")
    fetched = body.index("await fetch(")
    observed = body.rindex("performance.now()")
    assert gate < started, "the latency clock starts before the token gate — it would measure typing"
    assert started < fetched < observed, body


def test_the_tracer_imports_the_hook_and_floors_what_it_records():
    """trace.js owns the policy (api.js only observes), and the floor keeps the pollers out.

    Fifteen `setInterval` pollers run in this console (app.js 30 s, run.js 3 s,
    conversations.js 20 s …). Recording every request would bury the signal in its own volume,
    so only requests over the floor become rows — and the floor is a named constant, readable
    by whoever reads the store.
    """
    text = (STATIC / "trace.js").read_text(encoding="utf-8")
    assert re.search(r'import \{[^}]*onRequestTiming[^}]*\} from "/static/api\.js"', text), text
    floor = re.search(r"const SLOW_REQUEST_MS = (\d+)", text)
    assert floor, "the slow-request floor must be a named constant"
    assert 300 <= int(floor.group(1)) <= 2000, floor.group(1)
    assert "if (ms < SLOW_REQUEST_MS) return;" in text


def test_a_query_string_never_reaches_the_store():
    """A query value can carry what the operator typed (a search term, a filter), and this
    module records no free-text input — the trace module's standing contract. The PATH is kept
    whole, because which endpoint is slow is the entire question.
    """
    text = (STATIC / "trace.js").read_text(encoding="utf-8")
    assert re.search(r'function endpointOf\(path\) \{\s*return String\(path\)\.split\("\?"\)\[0\]',
                     text), text


@pytest.mark.parametrize("kind", ["slow", "load"])
def test_the_new_kinds_are_documented_where_the_store_is(kind):
    """A kind nobody can find is a kind nobody reads: the module docstring that explains the
    store has to name what it now holds."""
    doc = (Path(__file__).resolve().parents[1]
           / "src" / "rsched" / "web" / "api_traces.py").read_text(encoding="utf-8")
    head = doc[:doc.index('"""', doc.index('"""') + 3)]
    assert f"`{kind}`" in head, f"api_traces' docstring does not mention the {kind} kind"
