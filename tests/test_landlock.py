"""Landlock enforcement — REAL kernel tests (skipped where the kernel/seccomp can't):
the child wrapper jails a throwaway subprocess and we assert what it can and cannot do.
The decision logic itself (strict/permissive/off) is fully covered kernel-independently
in test_sandbox.py.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rsched import landlock

WRAPPER = str(Path(landlock.__file__).resolve())

needs_landlock = pytest.mark.skipif(
    landlock.abi_version() < 1, reason="kernel has no Landlock (or seccomp blocks it)")
needs_net_abi = pytest.mark.skipif(
    landlock.abi_version() < landlock.NET_ABI,
    reason=f"TCP restriction needs Landlock ABI {landlock.NET_ABI}")


def _run_jailed(spec: dict, code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, WRAPPER, json.dumps(spec), "--",
                           sys.executable, "-c", code, *args],
                          capture_output=True, text=True, timeout=60, check=False)


def _base_spec(*extra_rw: str) -> dict:
    # enough to start CPython: its prefixes + system trees; /proc for the interpreter.
    # Deliberately NO blanket /tmp — pytest's tmp_path lives there, and these tests assert
    # that paths OUTSIDE the spec (the tmp_path fixtures) are invisible.
    ro = ["/usr", "/bin", "/lib", "/lib64", "/etc", "/proc", sys.prefix, sys.base_prefix]
    return {"ro": ro, "rw": ["/dev", *extra_rw], "net": True}


@needs_landlock
def test_jailed_child_cannot_read_outside(tmp_path):
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("s3cret")
    r = _run_jailed(_base_spec(), f"open({str(secret)!r}).read()")
    assert r.returncode != 0 and "PermissionError" in r.stderr


@needs_landlock
def test_jailed_child_reads_and_writes_inside(tmp_path):
    allowed = tmp_path / "work"
    allowed.mkdir()
    (allowed / "in.txt").write_text("data")
    r = _run_jailed(_base_spec(str(allowed)),
                    f"p = {str(allowed)!r}; print(open(p + '/in.txt').read()); "
                    f"open(p + '/out.txt', 'w').write('done')")
    assert r.returncode == 0, r.stderr
    assert (allowed / "out.txt").read_text() == "done"


@needs_landlock
def test_ro_root_denies_write(tmp_path):
    visible = tmp_path / "ro"
    visible.mkdir()
    spec = _base_spec()
    spec["ro"].append(str(visible))
    r = _run_jailed(spec, f"open({str(visible)!r} + '/x', 'w')")
    assert r.returncode != 0 and "PermissionError" in r.stderr


@needs_net_abi
def test_net_none_denies_tcp_connect():
    spec = {**_base_spec(), "net": False}
    r = _run_jailed(spec, "import socket; socket.create_connection(('127.0.0.1', 9), 1)")
    assert r.returncode != 0 and "PermissionError" in r.stderr


@needs_landlock
def test_jail_inherited_by_grandchildren(tmp_path):
    """Landlock survives fork+exec — a util shelling out (the gu dispatcher) stays jailed."""
    secret = tmp_path / "deep-secret.txt"
    secret.write_text("x")
    code = ("import subprocess, sys; "
            "r = subprocess.run([sys.executable, '-c', "
            "'import sys; print(open(sys.argv[1]).read())', sys.argv[1]], "
            "capture_output=True, text=True); "
            "sys.exit(0 if 'PermissionError' in r.stderr else 1)")
    r = _run_jailed(_base_spec(), code, str(secret))
    assert r.returncode == 0, r.stderr + r.stdout


def test_wrapper_refuses_on_bad_spec():
    """The child wrapper NEVER runs the command when the jail can't close: garbage spec
    (and, on Landlock-less kernels, any spec) exits 97 without exec'ing."""
    r = subprocess.run([sys.executable, WRAPPER, "not-json", "--",
                        sys.executable, "-c", "print('leaked')"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == landlock.EXIT_SANDBOX_FAILED
    assert "leaked" not in r.stdout and "sandbox:" in r.stderr
    r = subprocess.run([sys.executable, WRAPPER, "{}"],   # no `--` separator
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == landlock.EXIT_SANDBOX_FAILED


def test_fs_mask_by_abi():
    assert landlock.fs_mask(0) == 0
    assert landlock.fs_mask(1) == (1 << 13) - 1
    assert landlock.fs_mask(3) == (1 << 15) - 1
    assert landlock.fs_mask(4) == (1 << 15) - 1        # ABI 4 adds net, no new fs bits
    assert landlock.fs_mask(5) == (1 << 16) - 1
    assert landlock.fs_mask(99) == (1 << 16) - 1       # future ABIs cap at what we know
