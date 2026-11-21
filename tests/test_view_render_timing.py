"""F861: how long a VIEW takes to settle after a navigation — the cost this console mostly is."""

from __future__ import annotations

import re
from pathlib import Path

from rsched.web.api_traces import KINDS

STATIC = Path(__file__).resolve().parents[1] / "static"
TRACE_JS = STATIC / "trace.js"


def test_the_render_kind_is_accepted_by_the_store():
    assert "render" in KINDS


def test_the_router_file_did_not_grow():
    """`static/app.js` is in `tests/size_ratchet.json` at its ceiling: the instrument must not
    cost it a single line, which is why the observer lives in the tracer instead."""
    lines = len((STATIC / "app.js").read_text(encoding="utf-8").splitlines())
    assert lines <= 398, f"app.js grew to {lines} lines — the size ratchet reds on that"


def test_the_measurement_starts_at_the_navigation_and_ends_when_the_view_stops_changing():
    """The shape of the quantity: armed by `hashchange`, closed by a quiet `#view`."""
    text = TRACE_JS.read_text(encoding="utf-8")
    assert "MutationObserver" in text, text
    assert re.search(r'getElementById\("view"\)', text), "the observer must watch #view"
    settle = re.search(r"const SETTLE_MS = (\d+)", text)
    assert settle and 50 <= int(settle.group(1)) <= 500, settle
    # armed on the navigation, and the elapsed measured from THAT instant
    assert re.search(r'addEventListener\("hashchange"', text)
    assert 'trace("render"' in text, text


def test_one_navigation_yields_at_most_one_row():
    """The run view streams: without disarming, a live view would file a row per frame."""
    text = TRACE_JS.read_text(encoding="utf-8")
    assert re.search(r"armed = false", text), "the observer must disarm after reporting"


def test_an_implausible_render_time_is_dropped_rather_than_recorded():
    """A backgrounded tab resumes minutes later and would otherwise book the whole absence as
    render time — the same class of lie F858's token-gate clock avoided."""
    text = TRACE_JS.read_text(encoding="utf-8")
    ceiling = re.search(r"const RENDER_MAX_MS = (\d+)", text)
    assert ceiling and int(ceiling.group(1)) <= 60000, ceiling
    assert "RENDER_MAX_MS" in text.split('trace("render"')[0], "the ceiling must gate the row"


def test_the_kind_is_documented_where_the_store_is():
    doc = (Path(__file__).resolve().parents[1]
           / "src" / "rsched" / "web" / "api_traces.py").read_text(encoding="utf-8")
    head = doc[:doc.index('"""', doc.index('"""') + 3)]
    assert "`render`" in head, "api_traces' docstring does not mention the render kind"
