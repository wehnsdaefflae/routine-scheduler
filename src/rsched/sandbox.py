"""Util-subprocess sandbox POLICY — what a util may see, decided daemon-side.

Every util subprocess (the `util` action, the vision fallback, write_util selftests, the
notify channel) runs inside a Landlock jail (landlock.py) whose visible filesystem derives
from the RUN's permissions: the routine's working dir + its fs_read_roots/fs_write_roots,
plus the fixed toolchain a util needs to execute at all (interpreter, uv + its cache, the
util library, system trees). Network is a per-util declaration (docstring `net:` line —
utils_run.util_needs): `none` (or undeclared) denies all TCP. Secrets scoping (declared-only
env injection, utils_run._child_env) is independent of this module and applies in every mode.

The server-config `sandbox:` mode is the escape hatch (docs/sandboxing.md):
- strict     — refuse to run utils when the jail can't engage as specified,
- permissive — engage when the kernel allows; warn once and proceed unsandboxed when not
               (the DEFAULT: a host without Landlock keeps working, a capable one is jailed),
- off        — never wrap (pre-0.61 behavior).

The child wrapper itself is always strict (landlock.py exits 97 rather than run unjailed);
every degradation decision is taken HERE, before launch, so it is loggable and testable.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import landlock

log = logging.getLogger("rsched.sandbox")

# System trees every util needs to EXECUTE at all (read+execute): interpreters, libraries,
# /etc (DNS, SSL certs, locale), /proc + /sys (uv, headless chromium), /run (resolved DNS),
# /var/log (the service-logs util). DAC still applies underneath — Landlock only ever
# SUBTRACTS access. The daemon-user's HOME is deliberately NOT here: the high-value targets
# (~/.config/routine-scheduler/secrets.env, ~/.credentials, ~/.ssh) stay invisible.
_SYSTEM_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/opt",
              "/run", "/sys", "/proc", "/var/log")
_SYSTEM_RW = ("/tmp", "/var/tmp", "/dev")  # noqa: S108 — jail roots, not temp-file handling
# HOME-scoped toolchain, read-only: launchers, the git/gh identity utils need to push with,
# and the host `claude` CLI install (a symlink into .local/share/claude on host installs; in
# the container it lives under /usr, already covered). The price of utils doing git/LLM work;
# documented in docs/sandboxing.md.
_HOME_RO = (".local/bin", ".local/share/claude", ".gitconfig", ".config/git", ".config/gh")
# HOME-scoped tool state, read-write: uv's script envs + managed pythons, XDG cache/state
# (playwright browsers, tool caches), and the claude CLI's session state — sanctioned tools
# of the system (the claude-cli endpoint uses the same state daemon-side).
_HOME_RW = (".cache", ".local/share/uv", ".local/state", ".claude", ".claude.json")


class SandboxRefusal(Exception):  # noqa: N818 — a refusal decision, not an error condition
    """Mode=strict and the sandbox cannot engage as specified — the util must not run."""


@dataclass(frozen=True)
class SandboxPolicy:
    """One caller's sandbox inputs: the server mode + the RUN-derived filesystem roots.
    Per-util facts (declared net, declared secrets) are resolved inside utils_lib at
    dispatch — this carries only what the run/permission layer knows.
    """

    mode: str = "permissive"
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] = ()


# Read-only asset dirs the util sandbox may see on EVERY run regardless of the calling
# routine — operator-staged host resources that no single routine owns. Currently the
# unpacked NopeCHA browser extension `launch-captcha-browser` loads with --load-extension
# (staged under the routines home's .control/; the free tier solves < ~100 CAPTCHAs/day with
# no key). Existence-guarded, so a deploy that has not staged it is unaffected. Read-only:
# a util may LOAD the extension, never write it.
_SHARED_RO_UNDER_ROUTINES = (".control/nopecha-extension",)


def _shared_read_roots(server) -> tuple[Path, ...]:
    home = getattr(server, "routines_home", None)
    if home is None:
        return ()
    return tuple(p for rel in _SHARED_RO_UNDER_ROUTINES
                 if (p := Path(home) / rel).exists())


def policy_for_ctx(ctx) -> SandboxPolicy:
    """The sandbox view of one LIVE run: its routine dir writable, its EFFECTIVE fs roots
    visible (config roots plus the run's one-time fs grants — RunContext.read_roots /
    write_roots, the same set the engine's own file actions honor), plus any
    operator-staged shared read-only asset dirs (e.g. the captcha-solver browser
    extension). One policy source, two enforcers: these roots and the engine path gates
    are compiled from the same grants.
    """
    return SandboxPolicy(mode=ctx.server.sandbox,
                         read_roots=(*ctx.read_roots(), *_shared_read_roots(ctx.server)),
                         write_roots=(ctx.routine.dir, *ctx.write_roots()))


def base_policy(server) -> SandboxPolicy:
    """The run-less sandbox view (write_util selftests, the web Library editor's selftest,
    the notify channel): toolchain + tmp + the util library only — no routine roots.
    """
    return SandboxPolicy(mode=server.sandbox)


def available() -> bool:
    """Can the jail engage at all on this kernel (filesystem rules, ABI ≥ 1)?"""
    return landlock.abi_version() >= 1


_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning("%s", message)


def _toolchain() -> tuple[list[str], list[str]]:
    home = Path.home()
    ro = [*_SYSTEM_RO, sys.prefix, sys.base_prefix,
          *(str(home / rel) for rel in _HOME_RO)]
    if uv := shutil.which("uv"):
        ro.append(str(Path(uv).resolve().parent))
    rw = [*_SYSTEM_RW, tempfile.gettempdir(), *(str(home / rel) for rel in _HOME_RW)]
    return ro, rw


def _ensure_write_roots(policy: SandboxPolicy) -> None:
    """A GRANTED write root that does not exist yet is SILENTLY dropped from the jail — the
    child wrapper attaches each Landlock rule to a live fd and skips paths it cannot open —
    so the very grant the user approved surfaces as a PermissionError on the util's first
    mkdir under it (R244/F293: a fresh WhatsApp session store broke every first pairing).
    The grant implies the directory: create missing write roots daemon-side, before the
    jail is assembled. Read roots are NOT created (reading a directory into existence would
    mask a config typo) — a missing one is warned about instead, once per path.
    """
    for root in policy.write_roots:
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
        except OSError as exc:   # e.g. the root is actually a file, or a parent is read-only
            _warn_once(f"wr:{root}", f"cannot create granted write root {root}: {exc}")
    for root in policy.read_roots:
        if not Path(root).exists():
            _warn_once(f"ro:{root}", f"granted read root {root} does not exist — "
                                     f"it grants nothing until created")


def wrap(cmd: list[str], *, policy: SandboxPolicy, libraries_home: Path,
         net: bool) -> list[str]:
    """The command that actually runs: `cmd` wrapped in the landlock.py child wrapper when
    the sandbox engages, `cmd` itself when the mode says (or allows) running bare. Raises
    SandboxRefusal when mode=strict and the jail can't close as specified — the caller
    turns that into the call's error observation. ONE jail composition for both callable
    kinds: utils AND per-routine scripts get the run's fs roots + the shared library
    read-only + the exec toolchain.
    """
    _ensure_write_roots(policy)   # mode-independent: the grant implies the directory
    if policy.mode == "off":
        return list(cmd)
    strict = policy.mode == "strict"
    if not available():
        msg = ("the util sandbox cannot engage: Landlock is unavailable "
               "(kernel < 5.13, LSM not enabled, or seccomp blocks it)")
        if strict:
            raise SandboxRefusal(
                f"{msg} — sandbox mode is 'strict', so utils will not run unsandboxed; "
                f"set `sandbox: permissive` (or off) in config.yaml, or enable Landlock")
        _warn_once("unavailable", f"{msg} — sandbox mode 'permissive': utils run UNSANDBOXED")
        return list(cmd)
    if not net and landlock.abi_version() < landlock.NET_ABI:
        msg = (f"this util declares net: none, but TCP denial needs Landlock ABI "
               f"{landlock.NET_ABI} (kernel has {landlock.abi_version()})")
        if strict:
            raise SandboxRefusal(f"{msg} — sandbox mode is 'strict', so the util will not run")
        _warn_once("net", f"{msg} — sandbox mode 'permissive': filesystem jail only")
        net = True
    ro, rw = _toolchain()
    ro.append(str(libraries_home))
    ro += [str(p) for p in policy.read_roots]
    rw += [str(p) for p in policy.write_roots]
    spec = {"ro": sorted(set(ro)), "rw": sorted(set(rw)), "net": bool(net)}
    wrapper = str(Path(landlock.__file__).resolve())
    return [sys.executable, wrapper, json.dumps(spec, separators=(",", ":")), "--", *cmd]
