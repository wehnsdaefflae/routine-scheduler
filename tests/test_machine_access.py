"""The setup wizard's key and install commands (machine_access.py): a generated key reads back
to the same public half, the pasted account line is idempotent when actually run, and the
agent line carries the shipped setup script and refuses a name it would splice unquoted."""

from __future__ import annotations

import base64
import re
import subprocess

import pytest

from rsched import machine_access as access
from rsched.paths import repo_root


def test_a_generated_key_reads_back_to_its_public_half():
    """The private half goes to Secrets and the public half is re-derived from it on every later
    step, so the two must agree — and the fingerprint is OpenSSH's own spelling."""
    pem, pub = access.generate_key("gpu-box")
    assert pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert pub.startswith("ssh-ed25519 ") and pub.endswith(" rsched-gpu-box")
    assert access.public_line(pem, "gpu-box") == pub
    assert re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", access.fingerprint(pub))
    with pytest.raises(ValueError):
        access.public_line("not a key", "gpu-box")


def test_the_key_var_is_named_for_the_machine():
    assert access.key_var_for("ubuntuserver-mark") == "UBUNTUSERVER_MARK_SSH_KEY"


def test_the_account_line_installs_once_however_often_it_is_pasted(tmp_path):
    """Run for real in a scratch HOME: the first paste creates ~/.ssh/authorized_keys with the
    forwarding-off line, the second finds the key and adds nothing."""
    _pem, pub = access.generate_key("box")
    cmd = access.install_command("account", pub, "mark")
    for _ in range(2):
        r = subprocess.run(["bash", "-c", cmd], env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        assert "rsched: key installed" in r.stdout
    lines = (tmp_path / ".ssh" / "authorized_keys").read_text(encoding="utf-8").splitlines()
    assert lines == [f"{access.KEY_OPTIONS} {pub}"]
    assert (tmp_path / ".ssh").stat().st_mode & 0o777 == 0o700


def test_the_agent_line_ships_the_setup_script_inline():
    """The box needs nothing but bash and sudo: the script travels base64 in the line, with the
    user, the restricted key and the co-owner as its environment."""
    _pem, pub = access.generate_key("box")
    cmd = access.install_command("agent", pub, "agent", "mark")
    assert cmd.startswith("sudo env AGENT_USER=agent ")
    assert f"AGENT_PUBKEY='{access.KEY_OPTIONS} {pub}'" in cmd and "AGENT_COOWNER=mark" in cmd
    b64 = re.search(r"echo (\S+) \| base64 -d", cmd).group(1)
    assert base64.b64decode(b64) == (repo_root() / access.SETUP_SCRIPT).read_bytes()
    assert access.install_command("agent", pub, "agent", sudo_stdin=True).startswith("sudo -S -p '' env ")


@pytest.mark.parametrize(("user", "coowner"), [("agent; rm -rf /", ""), ("agent", "$(id)"), ("Agent", "")])
def test_the_agent_line_refuses_a_name_it_would_splice_into_shell(user, coowner):
    _pem, pub = access.generate_key("box")
    with pytest.raises(ValueError):
        access.install_command("agent", pub, user, coowner)
    with pytest.raises(ValueError):
        access.install_command("root-shell", pub, "agent")
