"""Gate PREPARATION — what runs in the trusted child before any predicate executes.

`daemon/run_gate.py` owns admission: the deadline, the process group, the protocol and what a
decision does to the run. This module owns what happens between the child's start and the
predicate's exec: the built-in run reasons (`daemon/gate_builtin.py`), WHICH checks this fire
asks (the last run's own gate or the routine's — `gatecompose.py`), the BASELINE a check compares
against (the last run that finished ok), and the two jails — one for the declarative checks
(`gatekit.py`, built from what the listed check kinds need and nothing else) and one for a
custom `scripts/admit.py` (built from that script's own header).

Everything here runs in a killable child process, never on the daemon's event loop: reading a
routine's run history, resolving secrets and assembling a Landlock spec are filesystem work that
a hung mount could stall; the gate's one deadline covers them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import gatecompose, gatekit, sandbox, scriptenv, scripts, secrets, utils_header, utils_run
from ..config import RoutineConfig, ServerConfig
from ..paths import read_json
from ..registry import TERMINAL_STATES
from . import gate_builtin

RUN_DIR_RE = re.compile(r"\d{8}-\d{6}")
#: The custom predicate's script name. Not `gate`: three routines already run a `scripts/gate.py`
#: as an IN-RUN check runner; ticking the gate would have failed every one of their fires.
ADMIT = "admit"


class GateError(Exception):
    """Admission failed: never interpret an error as permission to skip or run."""


def last_ok(routine_dir: Path, current_run_id: str) -> tuple[dict | None, str]:
    """The baseline every "since the last run" check compares against.

    Returns `(last_ok, why_not)`: the newest ADMITTED run (a fire the gate skipped processed
    nothing and is passed over) as `{run_id, started, ended, fingerprints}` when it finished ok,
    or `(None, reason)` when it did not — a failed, partial or aborted run may have left work
    behind, so the gate must run. `(None, "")` means there is no earlier admitted run at all,
    which the checks that need a baseline already read as work.

    `ended` is when that run's status was last written — its finish, the moment it last read
    the finish line (`changed_since`). A status that cannot be stat'ed ends at its start, the
    reading that can only admit more.
    """
    runs = routine_dir / "runs"
    if not runs.is_dir():
        return None, ""
    current_ts = current_run_id.rpartition(":")[2]
    for run_dir in sorted((p for p in runs.iterdir() if p.is_dir()), reverse=True):
        if run_dir.name == current_ts or not RUN_DIR_RE.fullmatch(run_dir.name):
            continue
        status = read_json(run_dir / "status.json", {})
        if not isinstance(status, dict) or status.get("run_id") == current_run_id:
            continue
        if status.get("outcome") == "skipped":
            continue
        if status.get("state") not in TERMINAL_STATES:
            return None, f"run {run_dir.name} has not finished yet"
        if status.get("outcome") != "ok":
            return None, (f"the last run ({run_dir.name}) ended "
                          f"{status.get('outcome') or status.get('state')}, so it may have "
                          "left work behind")
        gate = read_json(run_dir / "gate.json", {})
        prints = gate.get("fingerprints") if isinstance(gate, dict) else None
        started = datetime.strptime(run_dir.name, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        try:
            ended = datetime.fromtimestamp((run_dir / "status.json").stat().st_mtime, UTC)
        except OSError:
            ended = started
        return {"run_id": status.get("run_id") or run_dir.name,
                "started": started.isoformat(), "ended": ended.isoformat(),
                "fingerprints": prints if isinstance(prints, dict) else {}}, ""
    return None, ""


#: The RECIPE files — any newer than the last ok run makes the next fire a run, whatever the
#: checks say, since a revised recipe can add work; a run reads them at BOOT, so a change counts
#: from when the last ok run STARTED. NOT `routine.yaml`: the operator ruled (R2813) that a config
#: edit leaves the gate applying as configured — use Run now to fire once (it skips admission).
CHANGE_SURFACE = ("tuning.yaml", "main.md")

#: The finish line counts from the moment the last ok run ENDED. Its finish reads the line
#: again — the accounting answers for every open outcome — and then STAMPS it
#: (`finishline.record`: a distance per open outcome, and the file itself for a routine whose
#: recipe has only a Done-when list). Read against the start, that stamp looked like the
#: operator changing the goal, and no gated routine whose runs keep an accounting could ever
#: be skipped.
FINISH_LINE = "state/finish-line.json"


def changed_since(routine_dir: Path, base: dict) -> str:
    """The first recipe file or finish line modified since the last ok run (`base`) read it, or
    "" — the recipe against its start, the finish line against its end; never routine.yaml.
    """
    try:
        started = datetime.fromisoformat(base["started"]).timestamp()
        ended = datetime.fromisoformat(base["ended"]).timestamp()
    except ValueError:
        return ""
    paths = [(routine_dir / rel, started) for rel in CHANGE_SURFACE]
    paths += [(path, started) for path in sorted((routine_dir / "stages").glob("*.md"))]
    paths.append((routine_dir / FINISH_LINE, ended))
    for path, since in paths:
        try:
            if path.stat().st_mtime > since:
                return str(path.relative_to(routine_dir))
        except OSError:
            continue
    return ""


def baseline_says_run(cfg: RoutineConfig, current_run_id: str) -> tuple[dict | None, str]:
    """`(last_ok, reason)` — a non-empty reason means the fire runs before any check is asked:
    the last admitted run did not finish ok, or this routine's recipe or finish line changed
    since it did.
    """
    base, why_not = last_ok(cfg.dir, current_run_id)
    if why_not:
        return None, why_not
    if base and (what := changed_since(cfg.dir, base)):
        return base, f"{what} changed since the last ok run"
    return base, ""


def _injectable(cfg: RoutineConfig, declared: set[str], optional: set[str]) -> dict[str, str]:
    """The secret values a gate may receive: declared AND (owned by the routine OR granted
    to it). A required secret that is neither is a configuration error, never a silent
    skip — a gate that cannot log in cannot know whether mail is waiting.
    """
    owned = secrets.load_routine_secrets(cfg.slug)
    central = secrets.load_secrets()
    granted = {k for k in declared if k in owned or cfg.grants.get(f"secret:{k}") is True}
    available = {**central, **owned}
    missing = declared - optional - (granted & available.keys())
    if missing:
        raise GateError("required gate secrets missing or unauthorized: "
                        + ", ".join(sorted(missing)))
    return {k: available[k] for k in granted & available.keys()}


def _scrubbed_env(injected: dict[str, str], declared: set[str]) -> dict[str, str]:
    env = utils_run.scoped_env(set(injected), injected, declared - set(injected))
    # Do not inherit credentials, Python injection, or a util dispatcher from daemon PATH.
    env = {k: v for k, v in env.items()
           if k in {"HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
           or k in injected}
    env["PATH"] = "/usr/bin:/bin"
    return env


def _wrap(cmd: list[str], policy: sandbox.SandboxPolicy, server: ServerConfig, *,
          net: bool, fs_roots: bool, fs_paths: tuple = ()) -> list[str]:
    try:
        return sandbox.wrap(cmd, policy=policy, libraries_home=server.libraries_home,
                            net=net, fs_roots=fs_roots, fs_paths=fs_paths)
    except sandbox.SandboxRefusal as exc:
        # Preserve the capability diagnosis, not the shared util-mode fallback advice.
        diagnosis = str(exc).split(" — ", 1)[0]
        raise GateError(
            f"{diagnosis}. Admission requires working strict Landlock filesystem/network "
            "isolation; enable the required kernel/LSM support, use an intentional manual "
            "run (which bypasses admission), or explicitly disable run_gate. Changing "
            "server sandbox mode cannot relax admission isolation.") from None


def prepare_script(cfg: RoutineConfig, server: ServerConfig) -> tuple[list[str], dict]:
    """The jail for the routine's own `scripts/admit.py`, built from its header."""
    root = cfg.dir.resolve()
    path = scripts.script_path(root, ADMIT).resolve(strict=True)
    if not path.is_relative_to(root / "scripts") or not path.is_file():
        raise GateError("scripts/admit.py must be a contained regular file")
    if path.stat().st_size > 262144:
        raise GateError("gate source exceeds 256 KiB")
    header = utils_header.parse_header(path.read_text(encoding="utf-8"))
    fs_lines = [line for line in header["doc"].splitlines()
                if line.strip().lower().startswith("fs:")]
    if len(fs_lines) > 1:
        raise GateError("duplicate gate fs declaration")
    if fs_lines:
        _, _, problems = utils_header.parse_fs(header["fs"])
        if problems:
            raise GateError("invalid gate filesystem declaration: " + "; ".join(problems))
    if header["calls"] or any(line.strip().lower().startswith("calls:")
                              for line in header["doc"].splitlines()):
        raise GateError("calls: is not supported for admission gates")
    if scripts.misdeclared(root, ADMIT) or scripts.call_problems(
            root, ADMIT, server.libraries_home):
        raise GateError("misdeclared gate metadata or util calls")
    if header["net"] not in ("", "none", "outbound"):
        raise GateError("invalid gate net declaration")
    declared = {v.upper() for v in header["secrets"]}
    injected = _injectable(cfg, declared, {v.upper() for v in header["optional_secrets"]})
    python = sys.executable
    if scriptenv.script_deps(root, ADMIT):
        py = scriptenv.venv_python(root)
        if not py.is_file():
            raise GateError(
                f"gate deps need a preprovisioned {scriptenv.VENV_DIR}; no admission-time installs")
        python = str(py)
    # Admission must never degrade silently, even on a permissive/off server.
    policy = sandbox.SandboxPolicy(
        mode="strict", own_dir=root,
        read_roots=(*cfg.fs_read_roots, *sandbox._shared_read_roots(server)),
        write_roots=tuple(cfg.fs_write_roots))
    cmd = _wrap([python, "-I", str(path)], policy, server, net=header["net"] == "outbound",
                fs_roots=header["fs_roots"], fs_paths=tuple(header["fs_paths"]))
    return cmd, _scrubbed_env(injected, declared)


def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == r or path.is_relative_to(r) for r in roots)


def prepare_checks(cfg: RoutineConfig, server: ServerConfig, context: dict,
                   current_run_id: str, *, admit: str = "", keep_going: bool = False,
                   asked: list[dict] | None = None,
                   source: dict | None = None) -> tuple[list[str], dict, str]:
    """The jail for the declarative checks and the argv context `gatekit.py` reads.

    The jail is exactly what the listed kinds need: the network only when a mail or url check
    is present, the secrets the checks name (granted or owned, like a script's), the folders
    and repositories they read — each of which must already be inside this routine's granted
    roots, so a check can never reach a path the routine itself could not — and, for
    `runs_since`, the fleet's run record. Returns `(cmd, env, early_run_reason)`; a non-empty
    reason means the baseline already decides "run" and no predicate needs to execute.

    `keep_going` (a routine that keeps jobs) asks the checks even then, carrying the reason
    that already decided — `admit`, or the baseline's — as the kit's `admit_reason`.

    `asked` are the checks this fire asks (`gatecompose.compose`; the routine's when None), and
    `source` says whose they are — the kit echoes it, so gate.json records it.
    """
    checks = [c for c in (cfg.run_gate.checks if asked is None else asked)
              if c.get("kind") != "script"]
    base, why_run = baseline_says_run(cfg, current_run_id)
    if why_run and not keep_going:
        return [], {}, why_run
    admit = admit or why_run
    root = cfg.dir.resolve()
    granted = [Path(p).resolve() for p in (*cfg.fs_read_roots, *cfg.fs_write_roots)]
    reads: list[Path] = []
    for path in gatekit.paths_named(checks, root):
        resolved = path.resolve()
        if not (_within(resolved, granted) or _within(resolved, [root])):
            raise GateError(f"gate check reads {path}, which is outside this routine's "
                            "filesystem roots — grant the folder first")
        reads.append(resolved)
    if any(c.get("kind") == "runs_since" for c in checks):
        reads.append(server.routines_home / ".control" / "workflow-usage.jsonl")
    kit = gatekit.ENTRY.parent
    declared = gatekit.secrets_named(checks)
    injected = _injectable(cfg, declared, set())
    policy = sandbox.SandboxPolicy(mode="strict", own_dir=root,
                                   read_roots=(kit, *reads), write_roots=())
    # `now` on the ROUTINE's clock — the zone its schedule fires in (validated at load) — so
    # a check asking what day it is today (`weekdays`, `dates`) reads the routine's calendar,
    # not the server's: the two disagree for hours around midnight whenever the zones differ.
    ctx = {**context, "version": 2, "routine_dir": str(root),
           "routines_home": str(server.routines_home),
           "libraries_home": str(server.libraries_home),
           "now": datetime.now(ZoneInfo(cfg.tz)).isoformat(),
           "last_ok": base, "checks": [{**c, "id": str(c.get("id") or f"c{i + 1}")}
                                       for i, c in enumerate(checks)],
           **({"admit_reason": admit} if admit else {}), **({"source": source} if source else {})}
    cmd = _wrap([sys.executable, "-I", str(gatekit.ENTRY), json.dumps(ctx)], policy, server,
                net=gatekit.needs_net(checks), fs_roots=True)
    return cmd, _scrubbed_env(injected, declared), ""


def child_main() -> None:
    """Trusted preparation child; stdin is private configuration, never predicate input."""
    try:
        payload = json.load(sys.stdin)
        cfg = RoutineConfig.model_validate(payload["routine"])
        server = ServerConfig.model_validate(payload["server"])
        mode = payload.get("mode") or "script"
        pending = gate_builtin.pending_inbox(cfg.dir)      # closures are not freight
        if mode == "inbox":
            sys.stdout.write(json.dumps({
                "version": 1, "decision": "run" if pending else "skip",
                "reason": "pending inbox" if pending else "inbox empty"}))
            return
        # the last run's own gate when it stands, else the routine's — never on a Test, which
        # asks the checks the operator is looking at (POST …/gate/test)
        asked, source = (gatecompose.compose(cfg) if payload["context"].get("reason") != "test"
                         else (list(cfg.run_gate.checks), {"kind": "routine"}))
        jobs_on = gate_builtin.keeps_jobs(cfg)
        builtin = gate_builtin.reason(cfg, server, asked, inbox=pending, jobs_on=jobs_on)
        if builtin and not (jobs_on and mode == "checks"):
            sys.stdout.write(json.dumps({
                "version": 1, "decision": "run", "reason": builtin,
                **({"checks": []} if mode == "checks" and not pending else {})}))
            return
        if mode == "checks":
            def jail(asked: list[dict], source: dict) -> tuple[list[str], dict, str]:
                return prepare_checks(cfg, server, payload["context"],
                                      payload["context"]["run_id"], admit=builtin,
                                      keep_going=jobs_on, asked=asked, source=source)
            try:
                cmd, env, early = jail(asked, source)
            except GateError as exc:
                if source["kind"] != "run":      # an ungranted path or secret in a RUN's gate
                    raise                        # hands the fire back to the routine's
                cmd, env, early = jail(list(cfg.run_gate.checks),
                                       {"kind": "routine", "rejected": str(exc)[:600]})
            if early:
                sys.stdout.write(json.dumps({"version": 1, "decision": "run",
                                             "reason": early, "checks": []}))
                return
            os.chdir(cfg.dir)
            os.execve(cmd[0], cmd, env)  # noqa: S606 — trusted command
        _base, why_run = baseline_says_run(cfg, payload["context"]["run_id"])
        if why_run:
            sys.stdout.write(json.dumps({"version": 1, "decision": "run", "reason": why_run}))
            return
        cmd, env = prepare_script(cfg, server)
        os.chdir(cfg.dir)
        os.execve(cmd[0], [*cmd, json.dumps(payload["context"])], env)  # noqa: S606
    except Exception as exc:
        sys.stderr.write(f"gate preparation failed: {type(exc).__name__}: {exc}\n")
        raise SystemExit(1) from None
