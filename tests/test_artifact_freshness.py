"""An artifact is a MUTABLE file under a STABLE name, so it is never cached (R1682).

The action documentation tells every run that re-writing a filename updates that artifact in
place — "extend what is there instead of making report-2.md". A run followed exactly that
instruction, rewrote `asks-2026-09-19.html` from 7,886 to 13,371 bytes adding two decision
cards, verified the new content on disk with a grep, and the user still saw the old version;
copying identical bytes to a NEW filename was the workaround that worked. A decision surface
therefore never reached him while the run recorded it delivered.

There is no check a routine can perform that distinguishes "written" from "visible", so the
guarantee belongs to the serving endpoint.
"""

from __future__ import annotations

import pytest

# the authenticated TestClient over a temp routines home, defined next to the other
# artifact-endpoint tests — re-exported rather than rebuilt so both exercise one server
from test_api import client as _api_client

client = pytest.fixture(name="client")(_api_client.__wrapped__)

CACHE_HEADER = "cache-control"


def _artifact(c, slug: str, path: str):
    return c.get(f"/api/routines/{slug}/artifact", params={"path": path})


def test_a_rewritten_artifact_serves_the_new_bytes_under_the_same_name(client):
    """The documented behaviour, asserted end to end: same URL, different bytes."""
    c, tmp = client
    art = tmp / "routines" / "apir" / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "asks.html").write_text("<h1>one card</h1>", encoding="utf-8")
    first = _artifact(c, "apir", "artifacts/asks.html")
    assert first.status_code == 200 and first.text == "<h1>one card</h1>"

    (art / "asks.html").write_text("<h1>one card</h1><h2>ask-047</h2>", encoding="utf-8")
    second = _artifact(c, "apir", "artifacts/asks.html")
    assert second.status_code == 200
    assert "ask-047" in second.text, "the rewritten artifact must be what is served"


def test_the_artifact_response_forbids_caching(client):
    """The mechanism that makes the assertion above hold for a real browser, which has its
    own cache the test client does not model: without an explicit directive a mutable file
    under a stable name is exactly what a client will happily re-use."""
    c, tmp = client
    art = tmp / "routines" / "apir" / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "r.html").write_text("<h1>x</h1>", encoding="utf-8")
    r = _artifact(c, "apir", "artifacts/r.html")
    assert r.status_code == 200
    assert "no-store" in r.headers.get(CACHE_HEADER, "").lower()


@pytest.mark.parametrize(("name", "body"), [("a.html", "<h1>a</h1>"), ("o.json", "{}")])
def test_every_deliverable_dir_is_served_uncached(client, name, body):
    """All three deliverable dirs serve through the same seam, so none of them may cache."""
    c, tmp = client
    base = tmp / "routines" / "apir"
    sub = "artifacts" if name.endswith(".html") else "output"
    (base / sub).mkdir(parents=True, exist_ok=True)
    (base / sub / name).write_text(body, encoding="utf-8")
    r = _artifact(c, "apir", f"{sub}/{name}")
    assert r.status_code == 200
    assert "no-store" in r.headers.get(CACHE_HEADER, "").lower()
