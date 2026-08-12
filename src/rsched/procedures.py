"""Per-routine PROCEDURES — the deterministic half of a routine (D88).

A routine is ONE thing with TWO interpreters (operator symmetry rule 2026-08-12): the
RECIPE is prose directing an LLM, a PROCEDURE is Python directing the interpreter — and
everything in the routine's settings applies to BOTH, identically: filesystem roots,
secret grants, OAuth connections, machine bindings, the util library, permissions and
rules. A procedure is ONE PEP 723 script `procedures/<name>.py` inside the routine's own
dir — private to the routine, versioned by its repo's autocommit, authored by the run
itself (`write_file`) or by the user — executed in a persistent venv in the routine's
workdir (`<routine>/.venv`, deps installed on first use, gitignored).

The shared envelope, layer by layer:

- FILESYSTEM: the jail is the run's own fs roots (`sandbox.wrap`, the same policy the
  recipe's file actions honor) — recipe and procedure read and write the SAME files.
- SECRETS / CONNECTIONS / MACHINES: the env carries every secret the routine is GRANTED
  (four-state `secret:` rows + the run overlay), its bound OAuth connection tokens, its
  bound machines, and the read-only RSCHED_API_TOKEN — the routine's standing settings,
  exactly what the recipe's tool calls can reach. The header `secrets:` line remains the
  ESCALATION hook: a declared, present, still-undecided secret files the same blocking
  exposure ask a util call would (interact.gate_procedure_secrets).
- UTILS: the shared library rides the jail read-only with `gu` on PATH — util access is
  part of the routine's permission surface, so both interpreters have it. A util invoked
  from a procedure runs inside the procedure's jail and env (it cannot widen either).
- RULES: prose is interpreted by the LLM, so rules reach a procedure through its
  rule-bound author — authoring or invoking a procedure never routes around a rule (the
  `procedures` permission doc carries the clause).
- ASKS: mid-run escalation (`ask_user`, blocking approvals) is inherently the LLM's
  channel — a procedure gets the routine's STANDING grants; anything more is requested
  recipe-side, exactly once, through the normal decision flow.

The `procedure` action is GATED by the `procedure` capability (the `procedures`
permission doc). There is deliberately NO approval dial: a procedure's blast radius is
the routine's own permissions, and the sandbox enforces those regardless of the code.
Header contract: first line `<name> — <summary>`, optional `usage:`, `net: outbound|none`
(undeclared = none → no TCP at exec; the dependency install is a net-open build step,
R40), `secrets:` for the escalation hook above.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from . import sandbox, utils_lib
from .ids import is_slug
from .paths import atomic_write

PROC_TIMEOUT_S = 300
VENV_DIR = ".venv"
_INSTALL_TIMEOUT_S = 300


def procedures_dir(routine_dir: Path) -> Path:
    return routine_dir / "procedures"


def script_path(routine_dir: Path, name: str) -> Path:
    return procedures_dir(routine_dir) / f"{name}.py"


def venv_dir(routine_dir: Path) -> Path:
    return routine_dir / VENV_DIR


def venv_python(routine_dir: Path) -> Path:
    return venv_dir(routine_dir) / "bin" / "python"


def exists(routine_dir: Path, name: str) -> bool:
    return is_slug(name) and script_path(routine_dir, name).is_file()


def list_procedures(routine_dir: Path) -> list[dict]:
    """The routine's own procedure catalog: {name, summary, usage} per script, from the
    docstring header. A script whose header cannot be parsed still lists (empty summary)
    — discoverability must not depend on hygiene.
    """
    d = procedures_dir(routine_dir)
    out: list[dict] = []
    for p in sorted(d.glob("*.py")) if d.is_dir() else []:
        try:
            header = utils_lib.parse_header(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # a broken script must not hide its siblings
            header = {"summary": "", "usage": ""}
        out.append({"name": p.stem, "summary": header.get("summary", ""),
                    "usage": header.get("usage", "")})
    return out


def needs(routine_dir: Path, name: str) -> tuple[set[str], bool, set[str]]:
    """(declared secret env vars, net-outbound?, the OPTIONAL subset) from the script's
    OWN header — no transitive graph: a procedure has no `calls:` siblings.
    """
    try:
        header = utils_lib.parse_header(
            script_path(routine_dir, name).read_text(encoding="utf-8"))
    except OSError:
        return set(), False, set()
    declared = {s.upper() for s in header["secrets"]}
    optional = {s.upper() for s in header["optional_secrets"]} & declared
    return declared, header["net"] == "outbound", optional


def script_deps(routine_dir: Path, name: str) -> list[str]:
    """The script's PEP 723 `dependencies`, from its `# /// script` block. [] when the
    block is absent or unparseable — a missing dep then fails visibly at exec.
    """
    try:
        text = script_path(routine_dir, name).read_text(encoding="utf-8")
    except OSError:
        return []
    lines, taking = [], False
    for ln in text.splitlines():
        if ln.strip() == "# /// script":
            taking = True
            continue
        if ln.strip() == "# ///":
            break
        if taking:
            lines.append(ln.removeprefix("# ").removeprefix("#"))
    try:
        meta = tomllib.loads("\n".join(lines)) if lines else {}
    except tomllib.TOMLDecodeError:
        return []
    deps = meta.get("dependencies")
    return [str(d) for d in deps] if isinstance(deps, list) else []


def _ensure_venv_ignored(routine_dir: Path) -> None:
    """Keep the venv out of the engine autocommit — `git add -A` would otherwise commit
    the whole interpreter tree into the routine's repo (mirrors outputs._ensure_ignored).
    """
    gi = routine_dir / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if any(ln.strip().rstrip("/") == VENV_DIR for ln in lines):
        return
    atomic_write(gi, "\n".join([*lines, "# the procedures venv (rebuilt on demand)",
                                f"{VENV_DIR}/", ""]))


def ensure_env(routine_dir: Path, name: str, *,
               policy: sandbox.SandboxPolicy, libraries_home: Path) -> str | None:
    """Create `<routine>/.venv` if missing and install the script's PEP 723 dependencies
    into it (a fast no-op when already satisfied). Returns an error line, or None. The
    install is a BUILD step: it runs net-open inside the routine jail (the util prewarm's
    R40 rationale — a `net: none` script must still be able to fetch its deps), and a
    failure surfaces instead of letting the exec die on an import it cannot explain.
    """
    _ensure_venv_ignored(routine_dir)
    py = venv_python(routine_dir)
    steps: list[list[str]] = []
    if not py.exists():
        steps.append(["uv", "venv", str(venv_dir(routine_dir))])
    if deps := script_deps(routine_dir, name):
        steps.append(["uv", "pip", "install", "--quiet", "--python", str(py), *deps])
    for step in steps:
        try:
            cmd = sandbox.wrap(step, policy=policy, libraries_home=libraries_home,
                               net=True)
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=_INSTALL_TIMEOUT_S, stdin=subprocess.DEVNULL,
                               cwd=str(routine_dir), check=False)
        except sandbox.SandboxRefusal as exc:
            return str(exc)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"venv setup failed ({step[1]}): {exc}"
        if r.returncode != 0:
            return (f"venv setup failed ({step[1]}): "
                    f"{(r.stderr or r.stdout).strip()[-400:]}")
    return None


def run_procedure(routine_dir: Path, name: str, args: list[str], *,
                  policy: sandbox.SandboxPolicy, libraries_home: Path,
                  env_secrets: dict[str, str] | None = None,
                  timeout: int = PROC_TIMEOUT_S) -> tuple[int, str, str]:
    """Controlled runner: the routine's own venv python on the script, the routine's
    settings as the env (`env_secrets` — the caller composes granted store secrets +
    connection tokens + machine bindings + the routine API token; every other store key
    is scrubbed), the shared jail (`sandbox.wrap` — run fs roots + library RO), `gu` on
    PATH, working directory = the routine dir so relative paths resolve like
    read_file/write_file. Returns (exit, out, err).
    """
    if not exists(routine_dir, name):
        have = ", ".join(p["name"] for p in list_procedures(routine_dir)) or "(none yet)"
        return 2, "", f"no procedure {name!r} (available: {have})"
    _declared, net, _opt = needs(routine_dir, name)
    if problem := ensure_env(routine_dir, name, policy=policy,
                             libraries_home=libraries_home):
        return 2, "", problem
    env_secrets = dict(env_secrets or {})
    env = utils_lib.scoped_env(set(env_secrets), env_secrets)
    env["PATH"] = f"{libraries_home}:{env.get('PATH', '')}"
    env["GLOBAL_UTILS_HOME"] = str(libraries_home)
    try:
        cmd = sandbox.wrap(
            [str(venv_python(routine_dir)), str(script_path(routine_dir, name)),
             *[str(a) for a in args]], policy=policy, libraries_home=libraries_home,
            net=net)
    except sandbox.SandboxRefusal as exc:
        return 2, "", str(exc)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(routine_dir), env=env,
                           check=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"procedure {name!r} timed out after {timeout}s"
    except OSError as exc:
        return 2, "", f"could not run procedure {name!r}: {exc}"
    return r.returncode, r.stdout, r.stderr
