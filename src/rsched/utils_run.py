"""RUNNING a util — the subprocess env, the sandbox, and the selftest.

Split out of `utils_lib.py` (F393): holding a library (paths, catalog, git) and EXECUTING from
it are different jobs, and this is the one with the blast radius. Every util subprocess runs
inside a Landlock jail scoped to the run's permissions (docs/sandboxing.md) and carries ONLY
the secrets its header declares — `scoped_env` is the declared-only injection gate, and it is
the reason an undeclared secret is unreachable rather than merely undocumented.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from . import sandbox
from .ids import is_slug
from .utils_header import parse_header
from .utils_lib import OUTPUT_CAP, exists, list_utils, read_util, util_dir

STRIP_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
              "OPENROUTER_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
              "SSH_AUTH_SOCK", "SSH_AGENT_PID")


class UtilNeeds(NamedTuple):
    """What one util's whole call tree declares it needs — the inputs the jail is built from."""

    secrets: set[str]
    net: bool
    optional: set[str]
    fs_roots: bool
    fs_paths: tuple[tuple[str, str], ...]


def util_needs(home: Path, name: str) -> UtilNeeds:
    """What one util declares, resolved TRANSITIVELY across its docstring `calls:` siblings —
    the whole call tree runs inside ONE jail and ONE env, so a caller inherits what its callees
    declared (gmail-body-dump calls gmail → gets the GMAIL_* secrets; anything calling a
    net: outbound sibling needs the network open too, and anything calling a sibling that
    declares a private path needs that path mounted). Undeclared = not granted: an unknown
    net line, a missing fs line, or none at all contributes nothing. A secret is optional only
    if EVERY declarer marks it `?` — one required declaration anywhere in the tree makes it
    required.

    The fs half only ever names what the jail MAY mount. Whether it actually does is decided
    in `sandbox.wrap`, against the grants the run holds — a declaration narrows, never widens.
    """
    secrets: set[str] = set()
    required: set[str] = set()
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
        secrets.update(declared)
        required.update(declared - opt)
        net = net or header["net"] == "outbound"
        fs_roots = fs_roots or header["fs_roots"]
        fs_paths += [p for p in header["fs_paths"] if p not in fs_paths]
        stack += header["calls"]
    return UtilNeeds(secrets, net, secrets - required, fs_roots, tuple(fs_paths))


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


def prewarm_script_deps(script: str, policy: sandbox.SandboxPolicy, home: Path) -> None:
    """Resolve + install a PEP 723 script's dependencies with the network OPEN, so a util
    whose runtime net policy is `none`/undeclared can still fetch its build-time deps (R40).
    Filesystem stays jailed (same policy); only this install phase gets TCP. Best-effort:
    any failure is swallowed — the caller's real run reports the genuine error. No-op under
    sandbox mode 'off' would still be a harmless local `uv sync`.
    """
    try:
        cmd = sandbox.wrap(["uv", "sync", "--script", script],
                           policy=policy, libraries_home=home, net=True,
                           fs_roots=False, fs_paths=())
    except sandbox.SandboxRefusal:
        return
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       stdin=subprocess.DEVNULL, cwd=str(home), check=False)
    except (OSError, subprocess.SubprocessError):
        return


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
    # File-backed capture + own process GROUP: `uv run` re-execs the script as a grandchild,
    # which a plain subprocess.run timeout never kills — it would survive the timeout and
    # keep the pipes open, blocking the engine turn forever. killpg reaps the whole tree,
    # and spool files (instead of PIPEs) bound memory however much the util prints.
    import signal
    import tempfile
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out_f, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err_f:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL,
                                text=True, env=env, cwd=str(cwd or home), start_new_session=True)
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

        def _read_capped(fh) -> str:
            fh.seek(0)
            text = fh.read(OUTPUT_CAP + 1)
            if len(text) > OUTPUT_CAP:
                text = text[:OUTPUT_CAP] + "\n[output truncated at 1 MB]"
            return text

        if timed_out:
            # F226: keep the stdout/stderr captured BEFORE the kill — a util that hung
            # AFTER printing diagnostics (the common case) would otherwise lose exactly
            # the material that explains why it hung. The timeout note rides on stderr.
            note = f"util {name!r} timed out after {timeout}s (process group killed)"
            partial_err = _read_capped(err_f)
            return -1, _read_capped(out_f), f"{partial_err}\n[{note}]" if partial_err else note
        return proc.returncode, _read_capped(out_f), _read_capped(err_f)


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
