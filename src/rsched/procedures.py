"""Per-routine PROCEDURES — the deterministic half of a routine (D88 phase 1).

A routine is a prose RECIPE the model interprets plus, where a responsibility is
deterministic (mail/feed polling, calculations on updated data, termination signaling),
PROCEDURE the Python interpreter runs. A procedure is ONE PEP 723 script
`procedures/<name>.py` inside the routine's OWN dir — private to the routine, versioned
by its repo's autocommit, authored by the run itself (`write_file` — an own-dir write
needs no grounding) or by the user.

Execution (operator-specified 2026-08-12): a procedure runs in a persistent **venv
inside the routine's workdir** (`<routine>/.venv`, created on first use, the script's
PEP 723 dependencies installed into it — gitignored, since autocommit is `git add -A`)
with **the routine's own filesystem permissions**: the jail is exactly the run's fs
roots (`sandbox.wrap_routine` — no library root, no `gu` on PATH), so the recipe's file
actions and the procedure read and write the SAME files. NOT the util sandbox: a util
sees the shared library and its ephemeral uv script env; a procedure sees the routine.

The `procedure` action is GATED by the `procedure` capability (the `procedures`
permission doc carries the conduct). Secrets stay declared-only behind the four-state
`secret:<NAME>` grants, `NAME?` optionals withheld rather than prompted (F290). There is
deliberately NO approval dial: a procedure's blast radius is the routine's own
permissions, and the sandbox enforces those regardless of what the script says.

The header contract is the util docstring standard minus the catalog lines: first line
`<name> — <summary>`, optional `usage:`, `net: outbound|none` (undeclared = none → no
TCP at exec time; the dependency-install phase is a build step and runs net-open, like
the util prewarm), `secrets:` naming every credential env var read. There is no `calls:`
graph: a step that needs a util's capability belongs in the recipe, not in a procedure.
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
               policy: sandbox.SandboxPolicy) -> str | None:
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
            cmd = sandbox.wrap_routine(step, policy=policy, net=True)
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
                  policy: sandbox.SandboxPolicy,
                  timeout: int = PROC_TIMEOUT_S,
                  extra_secrets: dict[str, str] | None = None,
                  withhold_secrets: set[str] | None = None) -> tuple[int, str, str]:
    """Controlled runner: the routine's own venv python on the script, scoped env
    (declared secrets only, minus withheld optionals), the ROUTINE jail
    (`sandbox.wrap_routine` — the run's fs roots, no library root), working directory =
    the routine dir so relative paths resolve like read_file/write_file. Returns
    (exit, out, err).
    """
    if not exists(routine_dir, name):
        have = ", ".join(p["name"] for p in list_procedures(routine_dir)) or "(none yet)"
        return 2, "", f"no procedure {name!r} (available: {have})"
    declared, net, _opt = needs(routine_dir, name)
    if problem := ensure_env(routine_dir, name, policy=policy):
        return 2, "", problem
    env = utils_lib.scoped_env(declared, extra_secrets, withhold_secrets)
    try:
        cmd = sandbox.wrap_routine(
            [str(venv_python(routine_dir)), str(script_path(routine_dir, name)),
             *[str(a) for a in args]], policy=policy, net=net)
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
