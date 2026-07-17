"""Minimal Landlock LSM binding (stdlib ctypes) — the util sandbox's kernel layer.

Two roles in one file so the daemon and the child share a single source of constants:

- IMPORTED by sandbox.py (the policy layer): `abi_version()` probes kernel support.
- EXECUTED as the child wrapper: ``python landlock.py '<spec-json>' -- CMD ARGS…``
  applies no_new_privs + the Landlock ruleset described by the spec to the current
  process (inherited by every descendant), then execs CMD in place. The child is
  STRICT by design: if any part of the spec cannot be applied it prints the reason
  to stderr and exits 97 — never runs the command unsandboxed. Degradation decisions
  (permissive/off) are the daemon's, taken BEFORE launching (sandbox.py), so the
  wrapper stays a pure mechanism. It runs standalone (no rsched imports) because the
  interpreter must read only this one file before the jail closes.

The spec is one JSON object: ``{"ro": [path, …], "rw": [path, …], "net": bool}`` —
ro paths get read+execute, rw paths the full filesystem mask for the kernel's ABI,
and net=false additionally denies ALL TCP bind/connect (Landlock ABI ≥ 4). UDP/ICMP
are not restrictable by Landlock today — the network boundary is TCP-only.

Hand-rolled on purpose: the PyPI `landlock` package is permanently dev-status and
lacks the ABI-4 network rules this sandbox needs (evaluated 2026-07-17).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

# Syscall numbers are identical across x86_64/aarch64/riscv (added post-unification).
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_PR_SET_NO_NEW_PRIVS = 38
_CREATE_RULESET_VERSION = 1 << 0   # flags: query the kernel's Landlock ABI version
_RULE_PATH_BENEATH = 1

# Filesystem access rights (linux/landlock.h). The mask a kernel HANDLES grows with its
# ABI — handling a bit unknown to the kernel is EINVAL, so the mask is version-keyed.
FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_TRUNCATE = 1 << 14              # ABI ≥ 3
FS_IOCTL_DEV = 1 << 15             # ABI ≥ 5
_FS_MASK_BY_ABI = {1: (1 << 13) - 1, 2: (1 << 14) - 1, 3: (1 << 15) - 1,
                   4: (1 << 15) - 1, 5: (1 << 16) - 1}
ACCESS_RO = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
# Rights that may appear in a rule whose target is a regular FILE (dir-only rights on a
# file rule are EINVAL).
_FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV

# TCP network rights (ABI ≥ 4). Handled with NO rules added = denied entirely.
NET_BIND_TCP = 1 << 0
NET_CONNECT_TCP = 1 << 1
NET_ABI = 4

EXIT_SANDBOX_FAILED = 97   # the wrapper's "jail could not close" exit code

_abi: int | None = None


class _RulesetAttr(ctypes.Structure):
    _fields_ = (("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64))


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


class SandboxApplyError(OSError):
    """A Landlock step failed — the jail could not close as specified."""


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def abi_version() -> int:
    """The kernel's Landlock ABI version (cached); 0 = unavailable (non-Linux kernel,
    Landlock not in the LSM list, or a seccomp filter blocking the syscall).
    """
    global _abi  # noqa: PLW0603 — one-probe cache: kernel support cannot change mid-process
    if _abi is None:
        _abi = 0
        if sys.platform == "linux":
            try:
                version = _libc().syscall(_SYS_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION)
                _abi = max(0, int(version))
            except OSError:
                _abi = 0
    return _abi


def fs_mask(abi: int) -> int:
    """Every filesystem access right the given ABI handles."""
    return _FS_MASK_BY_ABI[min(abi, max(_FS_MASK_BY_ABI))] if abi >= 1 else 0


def _check(result: int, step: str) -> None:
    if result < 0:
        err = ctypes.get_errno()
        raise SandboxApplyError(err, f"{step}: {os.strerror(err)}")


def _add_path_rule(libc: ctypes.CDLL, ruleset_fd: int, path: str, access: int) -> None:
    try:
        parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return   # a listed path that doesn't exist grants nothing — skip, don't fail
    try:
        if not os.path.isdir(path):  # noqa: PTH112 — stdlib-only file: no pathlib by design
            access &= _FILE_RIGHTS
        if not access:
            return
        attr = _PathBeneathAttr(access, parent_fd)
        _check(libc.syscall(_SYS_ADD_RULE, ruleset_fd, _RULE_PATH_BENEATH,
                            ctypes.byref(attr), 0), f"landlock_add_rule({path})")
    finally:
        os.close(parent_fd)


def apply(ro: list[str], rw: list[str], *, net: bool) -> None:
    """Restrict the CURRENT process (and all descendants) to the given filesystem view:
    read+execute beneath each `ro` path, full access beneath each `rw` path, everything
    else denied. net=False additionally denies all TCP bind/connect. Raises
    SandboxApplyError when the kernel can't enforce the spec — callers never get a
    silently weaker jail.
    """
    abi = abi_version()
    if abi < 1:
        raise SandboxApplyError(0, "Landlock is unavailable on this kernel")
    if not net and abi < NET_ABI:
        raise SandboxApplyError(
            0, f"net restriction needs Landlock ABI {NET_ABI}, kernel has {abi}")
    handled_fs = fs_mask(abi)
    attr = _RulesetAttr(handled_fs, 0 if net else NET_BIND_TCP | NET_CONNECT_TCP)
    size = ctypes.sizeof(_RulesetAttr) if abi >= NET_ABI else ctypes.sizeof(ctypes.c_uint64)
    libc = _libc()
    ruleset_fd = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), size, 0)
    _check(ruleset_fd, "landlock_create_ruleset")
    try:
        for path in ro:
            _add_path_rule(libc, ruleset_fd, path, ACCESS_RO & handled_fs)
        for path in rw:
            _add_path_rule(libc, ruleset_fd, path, handled_fs)
        _check(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "prctl(no_new_privs)")
        _check(libc.syscall(_SYS_RESTRICT_SELF, ruleset_fd, 0), "landlock_restrict_self")
    finally:
        os.close(ruleset_fd)


def main(argv: list[str]) -> int:
    """Child-wrapper entry: ``landlock.py '<spec-json>' -- CMD ARGS…`` — jail, then exec."""
    min_argv = 3   # spec, "--", cmd…
    if len(argv) < min_argv or argv[1] != "--":
        sys.stderr.write("usage: landlock.py '<spec-json>' -- CMD [ARGS…]\n")
        return EXIT_SANDBOX_FAILED
    try:
        spec = json.loads(argv[0])
        apply([str(p) for p in spec.get("ro") or []],
              [str(p) for p in spec.get("rw") or []], net=bool(spec.get("net")))
    except (ValueError, SandboxApplyError) as exc:
        sys.stderr.write(f"sandbox: {exc} — refusing to run the util unsandboxed\n")
        return EXIT_SANDBOX_FAILED
    cmd = argv[2:]
    try:
        os.execvp(cmd[0], cmd)  # noqa: S606 — the whole point: exec the util inside the jail
    except OSError as exc:
        sys.stderr.write(f"sandbox: cannot exec {cmd[0]!r}: {exc}\n")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
