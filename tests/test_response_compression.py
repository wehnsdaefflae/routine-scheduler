"""Responses are COMPRESSED when the client asks and the body is worth it."""

from __future__ import annotations

import json

import pytest
from starlette.responses import Response

from conftest import authed_client, make_test_server

CSP = "object-src 'none'; frame-ancestors 'self'"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="gz")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c


def test_a_large_payload_is_compressed_when_the_client_asks(client):
    """`/static/app.js` is the console's own bundle and is comfortably over the threshold —
    a real asset rather than a fixture, so the test cannot pass on a body this app never
    sends. TestClient decodes transparently, so the proof is the HEADER plus a body that is
    still the real asset.
    """
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip", dict(r.headers)
    assert len(r.text) > 1024


def test_the_compressed_bytes_are_really_smaller(client):
    """The saving is the reason this exists, so it is measured rather than assumed. The client
    decodes transparently, so the WIRE size is read off `Content-Length`, which the middleware
    rewrites to the compressed length — measured at 19,753 → 7,314 bytes for this asset.
    """
    plain = client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
    zipped = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers.get("content-encoding") == "gzip"
    wire = int(zipped.headers["content-length"])
    assert len(plain.content) > 1024
    assert wire < len(plain.content) // 2, (wire, len(plain.content))
    assert len(zipped.content) == len(plain.content)    # same document, fewer bytes on the wire


def test_a_small_body_is_left_alone(client):
    """`minimum_size=1024`: compressing a short JSON reply costs more than it saves, and one
    served-file test pins a 9-byte body by its exact `content-length`. A regression here shows
    up there as a failure nobody would connect to compression.
    """
    r = client.get("/api/status", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert len(r.content) < 1024, "fixture drift: /api/status grew past the threshold"
    assert "content-encoding" not in r.headers, dict(r.headers)
    assert r.headers.get("content-length") == str(len(r.content))


def test_a_client_that_does_not_ask_gets_identity(client):
    """Negotiation belongs to the client. A caller sending `identity` — a proxy, a probe, the
    `curl` this defect was measured with — must still receive plain bytes.
    """
    r = client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") is None, dict(r.headers)


def test_the_security_policy_survives_compression(client):
    """Order: gzip OUTSIDE `SecurityHeaders`, so the policy is set first and then compressed.
    Inverted, a compressed response could ship without the CSP — a silent loss of D219 that no
    CSP test would catch, because those responses are small.
    """
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert r.headers.get("content-security-policy") == CSP


def test_a_json_api_payload_over_the_threshold_is_compressed(client):
    """The operator's defect is an API PAYLOAD, not a static asset: `/api/questions` returns
    the whole fleet's question history (~1.1 MB measured) and `static/app.js` refetches it
    every 30 s per open tab. A hermetic client has no such history, so the contract is pinned
    on a route mounted for this test — the point being that `application/json` is compressed
    like any other text type, which is what makes the console's polling cheap.
    """
    big = json.dumps([{"id": i, "text": "x" * 200} for i in range(50)])

    @client.app.get("/api/_gziptest")
    def _payload() -> Response:
        return Response(big, media_type="application/json")

    r = client.get("/api/_gziptest", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert len(big) > 1024
    assert r.headers.get("content-encoding") == "gzip", dict(r.headers)
    assert int(r.headers["content-length"]) < len(big) // 2
    assert json.loads(r.text) == json.loads(big)
