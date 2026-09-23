"""The bearer door in front of the browser sidecar's two unauthenticated protocols.

`deploy/browser-auth-proxy.py` is not part of the package — it runs inside the chrome image,
which has no rsched install — so it is loaded here by path. That is the point of testing it at
all: it is the only thing standing between a `net: outbound` util and a browser holding live
site sessions, and a deploy script nobody imports is a deploy script nobody checks.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parents[1] / "deploy/browser-auth-proxy.py"


def _load():
    spec = importlib.util.spec_from_file_location("browser_auth_proxy", PROXY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


proxy = _load()


def _head(*lines: str) -> bytes:
    return ("GET /json/version HTTP/1.1\r\n" + "".join(f"{x}\r\n" for x in lines) + "\r\n").encode()


@pytest.mark.parametrize(("lines", "ok"), [
    ((["Authorization: Bearer sekrit"]), True),
    ((["authorization: bearer sekrit"]), True),          # header names and scheme are case-insensitive
    ((["Authorization: Bearer  sekrit  "]), True),       # surrounding whitespace is not the token
    ((["Host: 172.30.7.10:9222", "Authorization: Bearer sekrit"]), True),
    ((["Authorization: Bearer wrong"]), False),
    ((["Authorization: Bearer sekri"]), False),          # a prefix is not the token
    ((["Authorization: Basic sekrit"]), False),          # wrong scheme
    ((["X-Token: sekrit"]), False),                      # right value, wrong header
    (([]), False),
])
def test_only_a_matching_bearer_is_authorized(lines, ok):
    assert proxy._authorized(_head(*lines), "sekrit") is ok


def test_no_configured_token_authorizes_nothing():
    """Fail CLOSED. An unconfigured proxy must refuse, not forward: a browser port that looks
    guarded and is not is worse than one nobody believed in."""
    assert proxy._authorized(_head("Authorization: Bearer anything"), "") is False
    assert proxy._authorized(_head("Authorization: Bearer "), "") is False


def test_the_request_line_is_not_searched_for_the_token():
    """The scan starts after the request line, so a token pasted into the URL never authorizes —
    it would otherwise land in every access log and proxy trace on the path."""
    head = (b"GET /json/version?Authorization:%20Bearer%20sekrit HTTP/1.1\r\n"
            b"Host: 172.30.7.10:9222\r\n\r\n")
    assert proxy._authorized(head, "sekrit") is False


def _serve_once(handler_kwargs: dict, request: bytes) -> bytes:
    """Run the real handler over an in-process socket pair and return what the client got."""
    async def main() -> bytes:
        server = await asyncio.start_server(
            lambda r, w: proxy._handle(r, w, **handler_kwargs), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request)
            await writer.drain()
            body = await asyncio.wait_for(reader.read(4096), timeout=5)
            writer.close()
            return body
    return asyncio.run(main())

def test_an_unauthenticated_connection_is_refused_with_401_naming_the_way_in():
    got = _serve_once({"target": ("127.0.0.1", 1), "token": "sekrit", "label": "cdp"},
                      _head("Host: x"))
    assert got.startswith(b"HTTP/1.1 401 Unauthorized")
    # The refusal has to teach: a run that hits this needs to know it is a DECLARED SECRET,
    # not a network problem to route around.
    assert b"BROWSER_CDP_TOKEN" in got
    assert b"secrets:" in got


def test_an_unconfigured_proxy_refuses_every_connection_with_503():
    got = _serve_once({"target": ("127.0.0.1", 1), "token": "", "label": "cdp"},
                      _head("Authorization: Bearer anything"))
    assert got.startswith(b"HTTP/1.1 503")
    assert b"BROWSER_CDP_TOKEN" in got


def test_an_authorized_connection_reports_a_dead_upstream_rather_than_hanging():
    """Port 1 is nothing. The caller gets a named 502, which is what tells a run the browser
    sidecar is down rather than its own credentials being wrong."""
    got = _serve_once({"target": ("127.0.0.1", 1), "token": "sekrit", "label": "cdp"},
                      _head("Authorization: Bearer sekrit"))
    assert got.startswith(b"HTTP/1.1 502")
    assert b"upstream" in got.lower()
