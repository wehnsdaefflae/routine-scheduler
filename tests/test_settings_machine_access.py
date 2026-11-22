"""Settings → Machines wizard routes (web/settings/machine_access.py): `key` creates the login key
once and names it on the machine, later calls reuse it; `install-command` needs a key; `install`
logs in as the account the mode needs and never runs without a password."""

from __future__ import annotations

import pytest

from rsched import machine_access as access
from rsched import secrets

BOX = {"name": "box", "host": "10.0.0.9", "user": "mark",
       "host_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPinnedKey"}


@pytest.fixture
def client(api_client, tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "secrets.env")
    c, _ = api_client
    assert c.put("/api/settings/machines/box", json=BOX).status_code == 200
    return c


def test_the_key_is_created_once_and_named_on_the_machine(client):
    """The private half lands in Secrets under the machine's new key_var; a second call reuses
    it, because another machine may share that secret."""
    r = client.post("/api/settings/machines/box/key").json()
    assert r["generated"] is True and r["key_var"] == "BOX_SSH_KEY"
    pem = secrets.load_secrets()["BOX_SSH_KEY"]
    assert "PRIVATE KEY" not in r["public_key"] and access.public_line(pem, "box") == r["public_key"]
    assert client.app.state.server.machines["box"].key_var == "BOX_SSH_KEY"
    again = client.post("/api/settings/machines/box/key").json()
    assert again["generated"] is False and again["public_key"] == r["public_key"]


def test_the_install_command_needs_a_key_first(client):
    url = "/api/settings/machines/box/install-command"
    assert client.get(url).status_code == 409
    pub = client.post("/api/settings/machines/box/key").json()["public_key"]
    r = client.get(url, params={"mode": "account"}).json()
    assert pub.split()[1] in r["command"] and r["run_as"] == "as mark"
    assert client.get(url, params={"mode": "agent", "coowner": "$(id)"}).status_code == 400
    assert client.get(url, params={"mode": "nope"}).status_code == 400


def test_install_logs_in_as_the_account_the_mode_needs(client, monkeypatch):
    """Account mode logs in as the machine's own user; agent mode as the named admin, with sudo
    fed the password. No password, no login."""
    seen = []
    monkeypatch.setattr("rsched.machine_bootstrap.run_with_password",
                        lambda host, port, hk, user, pw, cmd, *, sudo: seen.append((user, sudo, cmd)) or {"ok": True})
    client.post("/api/settings/machines/box/key")
    url = "/api/settings/machines/box/install"
    assert client.post(url, json={"mode": "account", "password": ""}).status_code == 400
    assert client.post(url, json={"mode": "account", "password": "pw"}).json() == {"ok": True}
    assert client.post(url, json={"mode": "agent", "password": "pw", "login_user": ""}).status_code == 400
    assert client.post(url, json={"mode": "agent", "password": "pw", "login_user": "admin"}).status_code == 200
    (u1, s1, c1), (u2, s2, c2) = seen
    assert (u1, s1) == ("mark", False) and "authorized_keys" in c1
    assert (u2, s2) == ("admin", True) and c2.startswith("sudo -S -p '' env AGENT_USER=mark ")
