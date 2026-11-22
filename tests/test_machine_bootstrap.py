"""The wizard's password install (machine_bootstrap.py), against a fake `ssh` on PATH: the
password reaches ssh only through the askpass helper (and sudo's stdin when asked), never argv,
the pinned host key is the only one trusted, and a refusal is named as one."""

from __future__ import annotations

import pytest

from rsched import machine_bootstrap as boot

HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPinnedKey"

FAKE_SSH = """#!/bin/bash
for a in "$@"; do case "$a" in UserKnownHostsFile=*) kh="${a#UserKnownHostsFile=}";; esac; done
echo "argv=$*"
echo "askpass=$("$SSH_ASKPASS")"
echo "stdin=$(cat)"
echo "known=$(cat "$kh")"
exit "${FAKE_EXIT:-0}"
"""


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(FAKE_SSH, encoding="utf-8")
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    return monkeypatch


def test_known_hosts_lines_pin_the_catalog_key_for_host_and_port():
    assert boot.known_hosts_lines("h", 22, HOST_KEY) == [f"h {HOST_KEY}"]
    assert boot.known_hosts_lines("h", 2222, f"h {HOST_KEY}") == [f"[h]:2222 {HOST_KEY}"]
    assert boot.known_hosts_lines("h", 22, f"{HOST_KEY} me@laptop") == [f"h {HOST_KEY}"]
    assert boot.known_hosts_lines("h", 22, "garbage") == []


def test_the_password_travels_by_askpass_and_sudo_stdin_never_argv(fake_ssh):
    """The output a caller sees has the password masked even where the fake echoed it."""
    r = boot.run_with_password("box", 22, HOST_KEY, "mark", "s3cret!", "true", sudo=True)
    assert r["ok"] is True
    out = r["output"]
    assert "s3cret!" not in out
    assert "askpass=•••" in out and "stdin=•••" in out
    assert "mark@box true" in out and "StrictHostKeyChecking=yes" in out
    assert f"known=box {HOST_KEY}" in out


def test_no_sudo_means_nothing_on_stdin(fake_ssh):
    r = boot.run_with_password("box", 22, HOST_KEY, "mark", "pw", "true")
    assert "stdin=\n" in r["output"] + "\n"


def test_a_refused_password_is_named_as_one(fake_ssh):
    """ssh exits 255 with "Permission denied" (the fake echoes it through its argv line)."""
    fake_ssh.setenv("FAKE_EXIT", "255")
    r = boot.run_with_password("box", 22, HOST_KEY, "mark", "pw", "Permission denied")
    assert r["ok"] is False
    assert r.get("refused") is True and "copy command" in r["error"]


def test_an_unpinned_machine_is_never_offered_a_password():
    r = boot.run_with_password("box", 22, "", "mark", "pw", "true")
    assert r == {"ok": False, "error": "the machine has no pinned host key — scan it first"}


def test_a_host_key_mismatch_says_no_password_was_sent(fake_ssh):
    fake_ssh.setenv("FAKE_EXIT", "255")
    r = boot.run_with_password("box", 22, HOST_KEY, "mark", "pw", "Host key verification failed.")
    assert r["ok"] is False and "no password was sent" in r["error"]
