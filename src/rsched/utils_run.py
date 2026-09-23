"""RUNNING a util — the subprocess env, the sandbox, and the selftest.

Split out of `utils_lib.py` (F393): holding a library (paths, catalog, git) and EXECUTING from
it are different jobs, and this is the one with the blast radius. Every util subprocess runs
inside a Landlock jail scoped to the run's permissions (docs/sandboxing.md) and carries ONLY
the secrets its header declares — `scoped_env` is the declared-only injection gate, and it is
the reason an undeclared secret is unreachable rather than merely undocumented.

`run_jailed` is the ONE process runner all three callable kinds use — a util here, a routine's
own `scripts/<name>.py` (scripts.py) and the `shell` action (shellrun.py). The jail
COMPOSITION still differs per kind (each builds its own `sandbox.wrap` terms); the process
handling does not, and hand-copying it is what let two of the three lose the protections the
third documents.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import sandbox
from .captured_output import CapturedOutput, read_capped
from .ids import is_slug
from .utils_header import parse_header
from .utils_lib import OUTPUT_CAP, exists, list_utils, read_util, util_dir

log = logging.getLogger("rsched.utils_run")

#: What the DEADLINE exits with, for all three callable kinds. `timeout(1)`'s convention, and
#: the reason it must be POSITIVE: a negative `returncode` means "killed by signal N" on POSIX,
#: and `engine/executor._note_if_killed` reads one to raise `util_killed` — the health event
#: whose whole job is catching the cgroup OOM killer. This kind spelled it -1 until 0.366.2, so
#: every util that ran out of clock was filed as "killed by signal 1", a SIGHUP nothing in this
#: system sends: routine-improver's three timeouts on 2026-09-23 read as three kernel kills.
#: One deadline, one code — `shellrun` and `scripts` import this rather than restating it.
TIMEOUT_EXIT = 124

# Vars scrubbed from every jailed subprocess UNCONDITIONALLY (declared or not). LLM-auth: a
# util that needs an LLM (e.g. a `gu claude` equivalent) resolves its own credentials; it must
# never inherit the orchestrator's keys and silently mis-bill or use the wrong account.
# SSH agent: a forwarded agent in the daemon's env would let ANY net-capable util authenticate
# to hosts outside the machine catalog, routing around the per-routine binding — so the agent
# socket never reaches a util (remote machines carry their own scoped keys).
STRIP_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
              "OPENROUTER_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
              "SSH_AUTH_SOCK", "SSH_AGENT_PID")


class UtilNeeds(NamedTuple):
    """What one util's whole call tree declares it needs — the inputs the jail is built from.

    `tree` is the CALL TREE itself (the root plus every sibling reached through `calls:`,
    sorted). The jail terms above are what the tree declares; `tree` is who declared them,
    which is what the reserved-util gate needs — a `calls:` edge hands the callee's jail and
    its engine-injected credentials to the caller, so an ungated util reaching a gated one is
    a second door into that channel (grantpolicy._deny_util).
    """

    secrets: set[str]
    net: bool
    optional: set[str]
    fs_roots: bool
    fs_paths: tuple[tuple[str, str], ...]
    tree: tuple[str, ...] = ()


def util_needs(home: Path, name: str) -> UtilNeeds:
    """What one util declares, resolved TRANSITIVELY across its docstring `calls:` siblings —
    the whole call tree runs inside ONE jail and ONE env, so a caller inherits what its callees
    declared (gmail-body-dump calls gmail → gets the GMAIL_* secrets; anything calling a
    net: outbound sibling needs the network open too, and anything calling a sibling that
    declares a private path needs that path mounted). Undeclared = not granted: an unknown
    net line, a missing fs line, or none at all contributes nothing.

    OPTIONALITY IS THE ROOT'S CALL. A callee's `required` means "required when I run"; only the
    util actually being CALLED knows whether the code path that reaches that callee is taken.
    So a `?` on the ROOT's own `secrets:` line wins over a callee's required declaration, while
    among callees the strict rule still holds — one required declaration makes it required.
    Without this, three live utils demanded a credential on every call because a sibling they
    reach only under one flag rightly requires its own key (frame-fill → pangram under
    `--score`, R1818), and the caller met a secret-exposure prompt for work that never uses it.

    The fs half only ever names what the jail MAY mount. Whether it actually does is decided
    in `sandbox.wrap`, against the grants the run holds — a declaration narrows, never widens.
    """
    secrets: set[str] = set()
    required: set[str] = set()
    root_optional: set[str] = set()
    net = False
    fs_roots = False
    fs_paths: list[tuple[str, str]] = []
    seen: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        src = read_util(home, current)
        if src is None:
            continue
        header = parse_header(src)
        opt = {s.upper() for s in header["optional_secrets"]}
        declared = {s.upper() for s in header["secrets"]}
        if current == name:
            root_optional |= opt
        secrets.update(declared)
        required.update(declared - opt)
        net = net or header["net"] == "outbound"
        fs_roots = fs_roots or header["fs_roots"]
        fs_paths += [p for p in header["fs_paths"] if p not in fs_paths]
        stack += header["calls"]
    return UtilNeeds(secrets, net, secrets - (required - root_optional), fs_roots,
                     tuple(fs_paths), tuple(sorted(seen)))


def scoped_env(declared: set[str], extra_secrets: dict[str, str] | None = None,
               withhold: set[str] | None = None) -> dict:
    """A jailed subprocess's environment (utils AND per-routine scripts): the central
    secrets store injects ONLY `declared` vars; every other store key is scrubbed even
    when the daemon's own environment carries it — an undeclared secret must not reach
    the child by any route. STRIP_VARS (LLM keys) are removed unconditionally.

    `extra_secrets` are non-store secrets the engine resolves per run — today a routine's OAuth
    connection access tokens (<PROVIDER>_ACCESS_TOKEN). They obey the SAME rule: injected only
    if declared, scrubbed otherwise — the declared-only invariant covers them too.

    `withhold` names DECLARED vars to scrub anyway — the engine passes a run's not-granted
    OPTIONAL secrets (F290) so a public call runs without prompting; grant-free callers
    (CLI, selftest, notify, settings) pass nothing and inject every declared var as before.
    """
    from .secrets import load_secrets
    inject = {d.upper() for d in declared} - {w.upper() for w in (withhold or set())}
    env = {**os.environ}
    for key, value in {**load_secrets(), **(extra_secrets or {})}.items():
        if key.upper() in inject:
            env[key] = value
        else:
            env.pop(key, None)
    for k in STRIP_VARS:
        env.pop(k, None)                # never LLM keys: utils bill only via `gu claude`
    return env


def _child_env(home: Path, name: str, extra_secrets: dict[str, str] | None = None,
               withhold: set[str] | None = None) -> dict:
    """A util subprocess's environment: `scoped_env` over the util's transitive
    declarations (`calls:` siblings included — one jail, one env).
    """
    return scoped_env(util_needs(home, name).secrets, extra_secrets, withhold)


class Jailed(NamedTuple):
    """One jailed subprocess's outcome. `stdout`/`stderr` are `CapturedOutput` — already
    bounded, and carrying whether the capture itself lost anything.
    """

    returncode: int
    stdout: CapturedOutput
    stderr: CapturedOutput
    timed_out: bool


def _config_bytes(routine_dir: Path | None) -> bytes | None:
    """`routine.yaml`'s content, or None when there is none (or it cannot be read)."""
    if routine_dir is None:
        return None
    try:
        return (routine_dir / "routine.yaml").read_bytes()
    except OSError:
        return None


def _seal_broken(routine_dir: Path | None, before: bytes | None, label: str) -> str:
    """The one seal the JAIL cannot express, checked where it can be. `routine.yaml` sits in
    the routine's own directory, which every callable kind mounts read-write because it is the
    working directory — and Landlock unmasks access UP the path, so a narrower rule on one file
    beneath an allowed directory subtracts nothing. The action layer refuses `write_file
    routine.yaml`; a `shell` heredoc, a script or an `fs: roots` util is not on that path, and
    the escalation used to land silently and take effect at the NEXT run's boot.

    Detection, not prevention: the operator's own PATCH is a legitimate concurrent writer of
    this file (a Decisions-page `approve & apply` reaches a live run by design), so the line
    names both possibilities rather than reverting somebody's save (docs/sandboxing.md).
    """
    if routine_dir is None or _config_bytes(routine_dir) == before:
        return ""
    log.warning("routine.yaml changed while %s ran in %s — a run never writes its own config",
                label or "a jailed command", routine_dir)
    return (f"routine.yaml changed while {label or 'this command'} ran. Config is the user's "
            "and no run edits it — if this call wrote it, revert it and request the change "
            "with a deferred ask_user instead (if the operator just saved the routine page, "
            "this is that save)")


def run_jailed(cmd: list[str], *, env: dict, cwd: Path, timeout: int,
               label: str = "", cap: int = OUTPUT_CAP,
               config_seal: Path | None = None) -> Jailed:
    """Run one already-jailed command (`sandbox.wrap` composed `cmd`) and bring back at most
    `cap` characters of each stream. The ONE runner behind `util`, `script` and `shell`.

    Its three protections are why it is one function and not three copies:

    - OWN PROCESS GROUP (`start_new_session` + `killpg`): `uv run` re-execs the script as a
      GRANDCHILD, and a shell command can background one — neither is killed by a plain
      `subprocess.run` timeout, so the child outlives the deadline holding the pipes open and
      blocks the engine turn forever.
    - TEMPFILE CAPTURE read through `read_capped`, which takes `cap + 1` characters and never
      the file: `fh.read()` on a spool file puts the whole capture back in the daemon's memory,
      the 2026-09-14 incident (a 1.5 GB read, five hours of swap-thrash) in a seam `shell`
      reaches with one `find /`.
    - WHAT WAS PRINTED BEFORE THE KILL is kept: a command that hung after logging why it hung
      would otherwise lose exactly the material that explains the hang.

    `label` names the callable in the timeout and spawn-failure notes ("util 'x'",
    "script 'x'", "the command"). `config_seal` is a routine directory whose `routine.yaml`
    must not move across the call (`_seal_broken`).
    """
    before = _config_bytes(config_seal)
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out_f, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err_f:
        try:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f,
                                    stdin=subprocess.DEVNULL, text=True, env=env,
                                    cwd=str(cwd), start_new_session=True)
        except OSError as exc:
            return Jailed(2, CapturedOutput(""),
                          CapturedOutput(f"could not run {label or 'the command'}: {exc}"),
                          False)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
        notes = [f"{label or 'the command'} timed out after {timeout}s "
                 f"(process group killed)"] if timed_out else []
        notes += [n for n in (_seal_broken(config_seal, before, label),) if n]
        return Jailed(proc.returncode, read_capped(out_f, cap),
                      read_capped(err_f, cap, diagnostic="; ".join(notes)), timed_out)


def prewarm_script_deps(script: str, policy: sandbox.SandboxPolicy, home: Path) -> None:
    """Resolve + install a PEP 723 script's dependencies with the network OPEN, so a util
    whose runtime net policy is `none`/undeclared can still fetch its build-time deps (R40).
    Filesystem stays jailed (same policy); only this install phase gets TCP. Best-effort:
    the outcome is discarded — the caller's real run reports the genuine error. No-op under
    sandbox mode 'off' would still be a harmless local `uv sync`.
    """
    try:
        cmd = sandbox.wrap(["uv", "sync", "--script", script],
                           policy=policy, libraries_home=home, net=True,
                           fs_roots=False, fs_paths=())
    except sandbox.SandboxRefusal:
        return
    run_jailed(cmd, env={**os.environ}, cwd=home, timeout=180, label="the dependency prewarm")


def run_util(home: Path, name: str, args: list[str], *, timeout: int = 300,
             policy: sandbox.SandboxPolicy,
             extra_secrets: dict[str, str] | None = None,
             withhold_secrets: set[str] | None = None,
             cwd: Path | None = None) -> tuple[int, str, str]:
    """Controlled runner: only a named util from THIS library, uv-run, scoped env (declared
    secrets only, plus any `extra_secrets` the engine resolved for this run — same declared-only
    rule), library root on PATH (so the util can call siblings via `gu`), inside the Landlock jail
    `policy` + the util's own `net:` declaration describe (sandbox.wrap; the server `sandbox:` mode
    decides strict/permissive/off). Runs with working directory `cwd` — a routine's own dir for
    run-scoped calls, so relative paths a routine passes to a util resolve against ITS dir like
    read_file/write_file do — or the library `home` when unset (CLI, selftest, notify, settings).
    Returns (exit, out, err).
    """
    if not is_slug(name):
        return 2, "", f"invalid util name {name!r}"
    if not exists(home, name):
        return 2, "", f"no util named {name!r} (available: {[u['name'] for u in list_utils(home)]})"
    if not shutil.which("uv"):
        return 2, "", "uv is required to run utils but is not on PATH"
    env = _child_env(home, name, extra_secrets, withhold_secrets)
    env["PATH"] = f"{home}:{env.get('PATH', '')}"
    # Point the `gu` dispatcher (on PATH, for sibling calls) at THIS library, so a util that
    # shells out to `gu <sibling>` always resolves siblings here.
    env["GLOBAL_UTILS_HOME"] = str(home)
    # THE DEADLINE, so a util that waits on something slow can own its own clock instead of
    # racing this one. Both runners export it (scripts.run_script too) — a util that sets its
    # internal timeout from it reports what it captured; without it, two equal clocks expired
    # together, the killpg below won, and a remote exec that had already printed its job's PID
    # returned nothing at all (R1813, funscript-trainer 2026-09-21).
    env["RSCHED_UTIL_TIMEOUT_S"] = str(timeout)
    needs = util_needs(home, name)
    net = needs.net
    script = str(util_dir(home, name) / "main.py")
    # Build-time dependency install is a SEPARATE phase from the util's own execution: a
    # `net: none` util still needs PyPI to fetch its (non-cached) PEP 723 deps the first
    # time it runs — most visibly at write_util selftest. `uv run` would do resolve+install
    # under the util's OWN net policy and a net:none util could never install a third-party
    # dep at all (R40). So prewarm the deps in a network-OPEN, still-filesystem-jailed
    # `uv sync --script` (env lands in ~/.cache/uv, already a jail-RW toolchain root; it
    # writes nothing beside the script), THEN run offline-capable under the real policy.
    # Best-effort: a prewarm failure (offline host, no deps, older uv) is non-fatal — the
    # real run still surfaces the true error. `net` is util_needs' BOOL (True = outbound —
    # the old `!= "outbound"` string compare was vacuously true and prewarmed every call);
    # an outbound util installs inside its own net-open `uv run`, so it skips the pass here
    # and the selftest runner owns its warm-up instead (R20).
    if not net:
        prewarm_script_deps(script, policy, home)
    try:
        cmd = sandbox.wrap(["uv", "run", "--script", script, *args],
                           policy=policy, libraries_home=home, net=net,
                           fs_roots=needs.fs_roots, fs_paths=needs.fs_paths)
    except sandbox.SandboxRefusal as exc:
        return 2, "", str(exc)
    # F226: the timed-out leg still returns what was captured BEFORE the kill — a util that
    # hung AFTER printing diagnostics (the common case) would otherwise lose exactly the
    # material that explains why it hung. `run_jailed` owns that, the process group and the
    # bounded read; TIMEOUT_EXIT is the deadline's code, the same one for all three kinds.
    res = run_jailed(cmd, env=env, cwd=cwd or home, timeout=timeout, label=f"util {name!r}",
                     config_seal=policy.own_dir)
    return (TIMEOUT_EXIT if res.timed_out else res.returncode), res.stdout, res.stderr


def selftest(home: Path, name: str, *, timeout: int = 120,
             policy: sandbox.SandboxPolicy) -> tuple[bool, str]:
    # Build phase vs test phase (R20): run_util prewarms PEP 723 deps itself for
    # net:none/undeclared utils, but a net:outbound one (util_needs' bool: True) resolves
    # + installs its deps INSIDE `uv run` — so a first selftest of a heavy-dep script (a
    # cold pandas/scipy tree is a ~60 MB fetch plus a bytecode compile) would spend this
    # timeout on the toolchain and fail a correct util. Prewarm here (same best-effort
    # jail as the run path) so the timed window below covers the selftest, never the
    # install.
    net = util_needs(home, name).net
    if net:
        prewarm_script_deps(str(util_dir(home, name) / "main.py"), policy, home)
    code, out, err = run_util(home, name, ["--selftest"], timeout=timeout, policy=policy)
    if code == 0:
        return True, (err or out).strip()
    # F226: a FAILED selftest must surface ALL the diagnostics — the exit code plus BOTH
    # streams. The old `(err or out)` dropped the exit code and hid stdout whenever stderr
    # was non-empty, so a script printing its failure detail to stdout and a bare traceback
    # to stderr lost the detail. Label each stream; omit an empty one.
    parts = [f"exit {code}"]
    if out.strip():
        parts.append(f"stdout:\n{out.strip()}")
    if err.strip():
        parts.append(f"stderr:\n{err.strip()}")
    return False, "\n".join(parts)
