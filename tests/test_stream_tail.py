"""static/stream.js, the resilient transcript tail: reconnect backoff is jittered so correlated
drops (a daemon restart, a network blip) don't thunder back in lockstep, and an `online`
listener skips the backoff when connectivity returns — attached on start, detached by stop().
"""
from pathlib import Path

STREAM = Path(__file__).resolve().parents[1] / "static" / "stream.js"


def test_reconnect_backoff_is_jittered_and_online_aware():
    text = STREAM.read_text(encoding="utf-8")
    assert "Math.random()" in text, "backoff must carry jitter (correlated drops thunder back)"
    assert 'window.addEventListener("online"' in text
    assert 'window.removeEventListener("online"' in text, "stop() must detach the listener"


def test_a_tail_opens_its_stream_through_the_one_live_stream_wrapper():
    """Every socket goes through api.js's liveStream(), the only place the open-stream gauge
    is kept — a tail that built its own would be invisible to the reconnect/freeze traces."""
    text = STREAM.read_text(encoding="utf-8")
    assert "liveStream(events(base)" in text
    assert "new WebSocket" not in text and "new EventSource" not in text
