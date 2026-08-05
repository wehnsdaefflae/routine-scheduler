"""F263: the multi-run UI freeze is connection exhaustion — held EventSources approaching
the browser's ~6-per-origin HTTP/1.1 cap stall every later fetch with no error and no long
task. The hardening in static/stream.js: transcript tails hold at most MAX_TAIL_STREAMS
sockets between them (+ the global bus stays under the cap with headroom for fetches); a
tail that cannot get a slot delivers over REST polling and upgrades itself to SSE when a
slot frees; reconnect backoff is jittered so correlated drops don't re-exhaust the pool in
lockstep; and an `online` listener skips the backoff when connectivity returns.

Source-level guard (the console is no-build vanilla ES modules — same class as
test_static_imports); the tails' behavior end-to-end rides the existing tests/ui flows.
"""
from pathlib import Path

STREAM = Path(__file__).resolve().parents[1] / "static" / "stream.js"


def test_tail_stream_budget_and_poll_fallback():
    text = STREAM.read_text(encoding="utf-8")
    # the budget: strictly under the ~6/origin cap even with the global bus on top
    assert "MAX_TAIL_STREAMS = 3" in text
    assert "tailStreams >= MAX_TAIL_STREAMS" in text, (
        "open() must check the tail budget and fall back to polling — holding one socket "
        "per tail is exactly the exhaustion mechanism behind the F263 freeze")
    assert "POLL_MS" in text and "function poll()" in text
    # the poll must retry open() each round so it upgrades to SSE when a slot frees
    poll_body = text[text.index("function poll()"):]
    poll_body = poll_body[:poll_body.index("function reconnect")]
    assert "open()" in poll_body


def test_reconnect_backoff_is_jittered_and_online_aware():
    text = STREAM.read_text(encoding="utf-8")
    assert "Math.random()" in text, "backoff must carry jitter (correlated drops thunder back)"
    assert 'window.addEventListener("online"' in text
    assert 'window.removeEventListener("online"' in text, "stop() must detach the listener"


def test_every_stream_slot_is_released_on_close():
    """A slot leak would strand tails in polling mode forever — the release must ride the
    ONE close() helper every exit path (end event, error, stop) funnels through."""
    text = STREAM.read_text(encoding="utf-8")
    assert "const release = () => { if (held) { held = false; tailStreams -= 1; } }" in text
    close_at = text.index("const close = ()")
    close_body = text[close_at:text.index("async function catchUp")]
    assert "release()" in close_body
