"""`GET /api/credential-names` — the names of every stored credential, and never a value
(D171, operator decision 2026-10-08, option b).

The endpoint exists so `rsched export` can scrub credentials out of the off-box mirror WITHOUT
reading the stores the sandbox hides from it. That makes one property load-bearing above all
others: no response may ever carry a credential VALUE. Every store below is therefore seeded
with a distinctive value and the whole response body is asserted free of all of them — a
by-value assertion, because a by-key one would pass against a handler that happened to serve
`{"CENTRAL_API_KEY": "s3cr3t-central-value"}`.

The second property is the TIER: the export runs inside a util, under the routine token
(`RSCHED_API_TOKEN`), and that token is refused the whole `/api/settings` subtree plus every
routine's own secret names (`app.ROUTINE_TOKEN_DENIED_READS`). An endpoint placed under either
would be a 403 for its only caller, so the tier is pinned here rather than left to the route's
spelling.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched import secrets
from rsched.oauth import store as conn_store
from rsched.web.app import create_app

ROUTINE_TOKEN = "routine-tok"

#: Distinctive values, one per store, long enough to be real credentials.
CENTRAL_VALUE = "central-value-9f3a1c7e"
SCOPED_VALUE = "scoped-value-5b2d8e04"
OTHER_SCOPED_VALUE = "other-scoped-value-71ca96"
ACCESS_VALUE = "access-token-value-c4e7b2"
REFRESH_VALUE = "refresh-token-value-0d19af"
ALL_VALUES = (CENTRAL_VALUE, SCOPED_VALUE, OTHER_SCOPED_VALUE, ACCESS_VALUE, REFRESH_VALUE)


@pytest.fixture(autouse=True)
def _stores(monkeypatch, tmp_path):
    """Both secret scopes into tmp (scoped_path derives from secrets_path, so one patch moves
    both), and the OAuth store beside them."""
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "store" / "secrets.env")
    monkeypatch.setattr(conn_store, "connections_path",
                        lambda: tmp_path / "store" / "connections.json")
    secrets.set_secret("CENTRAL_API_KEY", CENTRAL_VALUE)
    secrets.set_routine_secret("alpha", "SFTP_USER", SCOPED_VALUE)
    secrets.set_routine_secret("beta", "BETA_TOKEN", OTHER_SCOPED_VALUE)
    conn_store.set_connection(conn_store.Connection(
        provider="notion", account="work",
        access_token=ACCESS_VALUE, refresh_token=REFRESH_VALUE))


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="alpha")
    server = make_test_server(tmp_path, routine_token=ROUTINE_TOKEN)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        yield c


def _get(client, token=TEST_TOKEN):
    return client.get("/api/credential-names", headers={"Authorization": f"Bearer {token}"})


def test_no_credential_value_ever_reaches_the_response(client):
    """THE property the endpoint is built on. Asserted over the raw body, so a value cannot
    hide in a key, a nested list or a field this test does not know about."""
    r = _get(client)
    assert r.status_code == 200
    body = json.dumps(r.json())
    for value in ALL_VALUES:
        assert value not in body, f"a credential VALUE reached the response: {value}"


def test_the_central_store_names_are_served(client):
    """Already in every run's prompt by D46 (names only, never a value), so serving them here
    tells a run nothing it was not told at boot."""
    assert "CENTRAL_API_KEY" in _get(client).json()["central"]


def test_scoped_names_are_served_as_an_unattributed_union(client):
    """D103: a scoped secret is invisible to every OTHER routine, which is why
    `/api/routines/{slug}/secrets` is denied to the routine token. A scrub list needs the
    names and never whose they are, so the attribution must not leave the daemon."""
    data = _get(client).json()
    assert data["scoped"] == ["BETA_TOKEN", "SFTP_USER"]        # sorted union, no slugs
    body = json.dumps(data)
    for slug in ("alpha", "beta"):
        assert slug not in body, f"the owning routine {slug} was attributed"


def test_connection_credential_fields_come_from_the_dataclass(client):
    """Taken from `Connection.__dataclass_fields__`, so a new token field is redacted the day
    it exists rather than the day someone remembers this list."""
    fields = _get(client).json()["connection_fields"]
    assert "access_token" in fields and "refresh_token" in fields
    assert "scopes" not in fields and "label" not in fields     # not credentials


def test_config_credential_fields_name_both_bearer_tiers(client):
    """`token` and `routine_token` are bootstrap.TOKEN_KEYS: the second one sits in EVERY
    config.yaml and is the one a mirror most easily carries off the box."""
    fields = _get(client).json()["config_fields"]
    assert "token" in fields and "routine_token" in fields and "api_key" in fields


def test_the_routine_token_may_read_it(client):
    """The export runs inside a util under RSCHED_API_TOKEN. An endpoint its only caller is
    refused is not a fix — this is why the route is NOT under `/api/settings`."""
    assert _get(client, ROUTINE_TOKEN).status_code == 200


def test_the_response_does_not_vary_with_what_the_values_are(client, tmp_path):
    """The same names with different values must produce the same body: proof that nothing in
    the response is derived from a value (a length, a hash, a prefix)."""
    before = _get(client).json()
    secrets.set_secret("CENTRAL_API_KEY", "an-entirely-different-value-6ba2")
    assert _get(client).json() == before


def test_an_empty_instance_serves_empty_name_lists(client, tmp_path):
    """No stores at all is a valid instance, not an error: the exporter must get a list it can
    redact nothing with rather than a 500 it cannot act on."""
    secrets.delete_secret("CENTRAL_API_KEY")
    secrets.drop_routine_secrets("alpha")
    secrets.drop_routine_secrets("beta")
    data = _get(client).json()
    assert data["central"] == [] and data["scoped"] == []
    assert data["connection_fields"], "the FIELD names are structural, not instance state"
