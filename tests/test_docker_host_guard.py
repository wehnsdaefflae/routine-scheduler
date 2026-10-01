"""A deploy script refuses before it drives a Docker daemon that is not on this host.

The defect (2026-10-01): `deploy/nat64.sh off` was run on omen-laptop inside the sshfs mount of
the SERVER's checkout. The script rewrote the server's `.env` through the mount, then talked to
the laptop's Docker Desktop, whose empty container list passed nat64.sh's only safety check. It
built rsched, rsched-tor and rsched-chrome on the laptop (~5.2 GB), pulled cliproxy and created
the project's browser network there; it stopped only because the default network's address pool
overlapped a laptop network. With a free subnet the laptop would have started a SECOND scheduler
daemon bound to its stale July copy of ~/routines. Meanwhile the server stayed on NAT64 while the
script printed "NAT64 off", and `status` run from the laptop reported nothing wrong.

Pinned here, against `deploy/docker-host-guard.sh` with stubbed `docker`/`hostname`/`findmnt`:
the daemon-name mismatch refuses, a network-filesystem checkout refuses BEFORE any docker call
(so a refusal leaves `.env` untouched), an unreachable daemon refuses, the matching host passes,
and both scripts that actually drive compose call the guard.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "deploy" / "docker-host-guard.sh"

#: Every deploy script that drives a Docker daemon, and the guard call it must carry. A new one
#: added without the guard is the same defect again, which is why this is a list and not a pair
#: of asserts.
DRIVERS = ("nat64.sh", "cliproxy-migrate-uid.sh")


def _stub_tree(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A throwaway checkout holding the real guard plus a caller, and a PATH whose `docker`,
    `hostname` and `findmnt` answer from environment variables.
    """
    bin_dir, deploy = tmp_path / "bin", tmp_path / "repo" / "deploy"
    deploy.mkdir(parents=True)
    bin_dir.mkdir()
    (deploy / "docker-host-guard.sh").write_text(GUARD.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    caller = deploy / "caller.sh"
    caller.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        '. "$(dirname -- "${BASH_SOURCE[0]}")/docker-host-guard.sh"\n'
        'require_local_docker_host "${1:-}" || exit 4\n'
        'echo PASSED\n', encoding="utf-8")
    caller.chmod(0o755)
    for name, body in (
            ("hostname", '#!/bin/sh\necho "${STUB_HOSTNAME:-ubuntuserver}"\n'),
            # `docker` also RECORDS that it was called, so a test can prove the filesystem
            # refusal happens before any daemon is contacted.
            ("docker", ('#!/bin/sh\ntouch "$STUB_DOCKER_CALLED"\n'
                        '[ -n "${STUB_DOCKER_FAIL:-}" ] && exit 1\n'
                        'echo "${STUB_DAEMON:-ubuntuserver}"\n')),
            ("findmnt", '#!/bin/sh\necho "${STUB_FSTYPE:-ext4}"\n')):
        p = bin_dir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "STUB_DOCKER_CALLED": str(tmp_path / "docker-was-called")}
    env.pop("DOCKER_HOST", None)
    return caller, env


def _run(caller: Path, env: dict[str, str], arg: str = "off", **stubs: str):
    return subprocess.run([str(caller), arg], capture_output=True, text=True,
                          env={**env, **stubs}, check=False)


def test_a_daemon_on_another_host_is_refused(tmp_path):
    """The reproduction case: Docker Desktop on the laptop, the server's checkout on the mount."""
    caller, env = _stub_tree(tmp_path)
    res = _run(caller, env, STUB_DAEMON="docker-desktop", STUB_HOSTNAME="omen-laptop")
    assert res.returncode == 4
    assert "PASSED" not in res.stdout
    # The refusal names BOTH sides and the way out — "wrong host" alone is not an instruction.
    assert "docker-desktop" in res.stderr
    assert "omen-laptop" in res.stderr
    assert "Run the command on 'docker-desktop'" in res.stderr


def test_the_same_host_passes(tmp_path):
    """The control: the guard must not stand between the operator and his own server."""
    caller, env = _stub_tree(tmp_path)
    res = _run(caller, env, STUB_DAEMON="ubuntuserver", STUB_HOSTNAME="ubuntuserver")
    assert res.returncode == 0
    assert "PASSED" in res.stdout


@pytest.mark.parametrize("fstype", ["fuse.sshfs", "nfs", "cifs"])
def test_a_checkout_on_a_network_filesystem_is_refused_before_any_docker_call(tmp_path, fstype):
    """Checked FIRST and without a daemon: this is the signal that says the files about to be
    rewritten belong to another machine, and nat64.sh's promise is that a refusal leaves `.env`
    exactly as it was.
    """
    caller, env = _stub_tree(tmp_path)
    res = _run(caller, env, STUB_FSTYPE=fstype)
    assert res.returncode == 4
    assert fstype in res.stderr
    assert not Path(env["STUB_DOCKER_CALLED"]).exists(), "the daemon was contacted anyway"


def test_an_unreachable_daemon_is_refused_with_what_to_check(tmp_path):
    caller, env = _stub_tree(tmp_path)
    res = _run(caller, env, STUB_DOCKER_FAIL="1")
    assert res.returncode == 4
    assert "no Docker daemon answered" in res.stderr


@pytest.mark.parametrize("script", DRIVERS)
def test_every_script_that_drives_compose_calls_the_guard(script):
    """The guard only helps where it is called, and the next deploy script will be written by
    copying one of these.
    """
    text = (REPO / "deploy" / script).read_text(encoding="utf-8")
    assert "docker-host-guard.sh" in text, f"deploy/{script} sources no host guard"
    assert "require_local_docker_host" in text, f"deploy/{script} never calls the guard"


def test_nat64_guards_the_write_and_the_report(tmp_path):
    """Both of nat64.sh's blind spots: `switch` wrote `.env` before any check, and `status` run
    from the wrong host listed no containers and reported nothing wrong.
    """
    text = (REPO / "deploy" / "nat64.sh").read_text(encoding="utf-8")
    switch, status = text.index("switch() {"), text.index("status() {")
    # In `switch`, the guard must come before the first put_env — the .env write.
    body = text[switch:status]
    assert "require_local_docker_host" in body
    assert body.index("require_local_docker_host") < body.index('put_env "'), \
        "nat64.sh writes .env before it checks the host"
    assert "require_local_docker_host" in text[status:], "nat64.sh status reports any host's fleet"
