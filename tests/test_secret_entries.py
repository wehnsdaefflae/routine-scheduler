"""JSON-map secret entries: add/replace one entry (merged SERVER-side, so the other entries' values
are never returned), delete one, and the listing exposes entry NAMES only — never values."""

from __future__ import annotations

import json

from rsched import secrets as sstore


def test_map_secret_entry_crud(api_client):
    client, _ = api_client
    # the first entry creates the map
    r = client.put("/api/settings/secrets/FTP_SOURCES/entry",
                   json={"name": "acme", "value": {"host": "h", "user": "u", "pass": "p"}})
    assert r.status_code == 200, r.text
    body = client.get("/api/settings/secrets").json()
    assert body["maps"]["FTP_SOURCES"] == ["acme"]
    assert "FTP_SOURCES" in body["keys"]

    # a SECOND entry without re-sending the first — the merge happens server-side
    client.put("/api/settings/secrets/FTP_SOURCES/entry",
               json={"name": "beta", "value": {"host": "h2", "user": "u2", "pass": "p2"}})
    body = client.get("/api/settings/secrets").json()
    assert body["maps"]["FTP_SOURCES"] == ["acme", "beta"]
    stored = json.loads(sstore.load_secrets()["FTP_SOURCES"])
    assert stored["acme"]["host"] == "h" and stored["beta"]["host"] == "h2"   # both preserved
    assert "h2" not in json.dumps(body["maps"])                               # listing leaks no values

    # delete one entry, then the last (which drops the whole secret)
    assert client.delete("/api/settings/secrets/FTP_SOURCES/entry/acme").status_code == 200
    assert client.get("/api/settings/secrets").json()["maps"]["FTP_SOURCES"] == ["beta"]
    client.delete("/api/settings/secrets/FTP_SOURCES/entry/beta")
    body = client.get("/api/settings/secrets").json()
    assert "FTP_SOURCES" not in body.get("maps", {})
    assert "FTP_SOURCES" not in body["keys"]


def test_entry_on_non_map_secret_rejected(api_client):
    client, _ = api_client
    client.put("/api/settings/secrets", json={"key": "PLAIN", "value": "just-a-token"})
    r = client.put("/api/settings/secrets/PLAIN/entry", json={"name": "x", "value": {"a": 1}})
    assert r.status_code == 400
    assert "non-JSON-object" in r.text


def test_delete_missing_entry_404(api_client):
    client, _ = api_client
    assert client.delete("/api/settings/secrets/FTP_SOURCES/entry/nope").status_code == 404
