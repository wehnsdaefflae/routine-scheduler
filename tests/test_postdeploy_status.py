"""`rsched postdeploy`'s status READ — what kind of nothing came back, and asking again (0.563.0)."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rsched import cli_postdeploy
from rsched.postdeploy import FAIL, NO_ANSWER, served_identity


def _serve(code: int, body: bytes):
    """A one-route HTTP server on an ephemeral port, answering every GET with `code`/`body`."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def served():
    servers = []

    def make(code, body):
        srv = _serve(code, body)
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_a_status_payload_comes_back_with_no_why(served):
    url = served(200, json.dumps({"version": "0.563.0"}).encode())
    assert cli_postdeploy._status(url, "t") == ({"version": "0.563.0"}, "")


@pytest.mark.parametrize(("code", "body", "why"), [
    (401, b"", "auth"), (403, b"", "auth"), (500, b"boom", "server-error"),
    (200, b"<html>", "not-status"), (200, b"[1, 2]", "not-status"),
])
def test_each_answer_that_is_not_a_status_is_named(served, code, body, why):
    assert cli_postdeploy._status(served(code, body), "t") == (None, why)


def test_a_closed_port_is_refused_and_a_silent_one_is_a_timeout(monkeypatch):
    """The two the 2026-11-20 session confused: nothing listening, and listening but silent."""
    with socket.socket() as probe:              # a port that was free a moment ago
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    assert cli_postdeploy._status(f"http://127.0.0.1:{closed}", "t") == (None, "refused")

    monkeypatch.setattr(cli_postdeploy, "STATUS_TIMEOUT_S", 0.3)
    with socket.socket() as silent:             # accepts (the kernel's backlog), never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        url = f"http://127.0.0.1:{silent.getsockname()[1]}"
        assert cli_postdeploy._status(url, "t") == (None, "timeout")


def test_a_url_with_no_scheme_is_bad_url_not_a_traceback():
    assert cli_postdeploy._status("127.0.0.1:8321", "t") == (None, "bad-url")


def test_a_busy_daemon_is_asked_again_until_it_answers(monkeypatch):
    """The specimen: two timeouts during the boot burst, then the status."""
    answers = iter([(None, "timeout"), (None, "timeout"), ({"version": "0.563.0"}, "")])
    monkeypatch.setattr(cli_postdeploy, "_status", lambda url, token: next(answers))
    monkeypatch.setattr(cli_postdeploy, "_sleep", lambda s: None)
    said = []

    status, why, attempts = cli_postdeploy.await_status("http://x", "t", 60, said.append)

    assert (status, why, attempts) == ({"version": "0.563.0"}, "", 3)
    assert len(said) == 2 and "timeout on attempt 1" in said[0]


@pytest.mark.parametrize("why", ["auth", "bad-url", "not-status", "unreachable"])
def test_an_answer_time_cannot_change_is_not_waited_for(monkeypatch, why):
    calls = []
    monkeypatch.setattr(cli_postdeploy, "_status",
                        lambda url, token: calls.append(1) or (None, why))
    monkeypatch.setattr(cli_postdeploy, "_sleep", lambda s: pytest.fail("waited"))

    assert cli_postdeploy.await_status("http://x", "t", 60) == (None, why, 1)


def test_the_wait_is_bounded(monkeypatch):
    """A port that never answers is still the finding — reported after the wait, not never."""
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(cli_postdeploy.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli_postdeploy, "_status", lambda url, token: (None, "refused"))
    monkeypatch.setattr(cli_postdeploy, "_sleep", lambda s: None)

    status, why, attempts = cli_postdeploy.await_status("http://x", "t", 30)

    assert (status, why) == (None, "refused") and 1 < attempts < 10


def test_no_wait_means_one_attempt(monkeypatch):
    monkeypatch.setattr(cli_postdeploy, "_status", lambda url, token: (None, "timeout"))
    monkeypatch.setattr(cli_postdeploy, "_sleep", lambda s: pytest.fail("waited"))
    assert cli_postdeploy.await_status("http://x", "t", 0) == (None, "timeout", 1)


def test_each_why_is_worded_as_its_own_next_move():
    """A busy daemon is no longer told to check which host it is on."""
    busy = served_identity("http://127.0.0.1:8321", None, "config bind/port", "timeout", 7)
    down = served_identity("http://127.0.0.1:8321", None, "config bind/port", "refused", 7)

    assert busy.verdict == down.verdict == FAIL
    assert "in 7 attempts" in busy.detail and "up but busy" in busy.detail
    assert "right host" not in busy.detail
    assert "nothing is listening" in down.detail and "right host" in down.detail
    for detail in (busy.detail, down.detail):
        assert detail.startswith("no answer from http://127.0.0.1:8321 (config bind/port)")
        assert "nothing below is a verdict" in detail
    assert set(NO_ANSWER) >= set(cli_postdeploy.RETRYABLE)
