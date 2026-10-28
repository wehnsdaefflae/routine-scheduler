"""The console's Content-Security-Policy, as it is actually sent (D219).

The policy is NARROW on purpose, and this file is where that narrowness is pinned. A blob
iframe INHERITS the console's policy, so a `script-src` would stop every HTML artifact's inline
scripts from running (`components/blobtab.js` opens them sandboxed, and the artifact panel frames
them the same way); `index.html` carries one inline theme script; `el()` sets `style=` everywhere.
So the two directives shipped are the two that cost the console nothing it does: no viewer uses
`<embed>` or `<object>` (a PDF renders in an `<iframe>`, which `object-src` does not govern), and
every frame the console builds is its own origin or a blob of it — the relayed noVNC screens
(`/browser-view`, `/desktop-view`, served from this origin precisely so they are same-origin), the
Help pages under `/docs`, and the artifact frames.

The widening that becomes possible once artifacts are served from their own route (a hashed theme
script, `style-src`, a `frame-src` naming the relay) is a LATER increment:
`test_the_policy_carries_no_other_directive` is what makes adding one a deliberate act with a
test to change, rather than a one-line edit that silently breaks every HTML artifact.

The header string is written out LITERALLY here instead of imported from the module that sends
it: a test comparing a constant with itself pins nothing.
"""

from __future__ import annotations

import pytest

from conftest import authed_client, make_test_server

CSP = "object-src 'none'; frame-ancestors 'self'"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="csp")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/sw.js", "/manifest.webmanifest", "/static/app.js",
                                  "/api/status"])
def test_every_console_response_carries_the_policy(client, path):
    """The console's own page, the two root-scoped files, a static asset and an API read — one
    header on everything the middleware sees, so no route is born without it."""
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.headers.get("content-security-policy") == CSP


def test_the_policy_carries_no_other_directive(client):
    """`script-src`/`default-src` is the directive that would break the artifact viewer, and
    `style-src` the one `el()`'s inline styles would need. Neither is shipped: this increment is
    exactly two directives, and the set is the claim."""
    policy = client.get("/").headers["content-security-policy"]
    named = {d.strip().split(" ")[0] for d in policy.split(";") if d.strip()}
    assert named == {"object-src", "frame-ancestors"}


def test_the_refusal_an_unauthenticated_caller_gets_carries_it_too(client):
    """`frame-ancestors` matters most on the responses nobody authenticated for — those are the
    ones a hostile page would try to frame. The 401 comes out of the auth dependency, inside the
    header pass, so it carries the policy like any other response."""
    r = client.get("/api/status", headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401
    assert r.headers.get("content-security-policy") == CSP


def test_a_live_stream_still_connects(client):
    """A header belongs to an http response, and an http middleware never sees a websocket scope
    (the same reason the timing middleware cannot count a stream). The ticket mint is http and
    carries the policy; the socket it is spent on still completes its handshake."""
    r = client.post("/api/stream-ticket")
    assert r.status_code == 200
    assert r.headers.get("content-security-policy") == CSP
    with client.websocket_connect(f"/api/events?ticket={r.json()['ticket']}"):
        pass
