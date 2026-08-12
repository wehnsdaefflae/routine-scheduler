"""Per-routine PROCEDURES — the deterministic half of a routine (D88 phase 1).

A routine is a prose RECIPE the model interprets plus, where a responsibility is
deterministic (mail/feed polling, calculations on updated data, termination signaling),
PROCEDURE the Python interpreter runs. A procedure is ONE PEP 723 script
`procedures/<name>.py` inside the routine's OWN dir — private to the routine, versioned
by its repo's autocommit, authored by the run itself (`write_file` — an own-dir write
needs no grounding) or by the user.

Execution is the `procedure` action, GATED by the `procedure` capability (the
`procedures` permission doc carries the conduct). A procedure runs in the SAME Landlock
jail a util gets — this run's fs roots, TCP only with `net: outbound` — with the SAME
declared-only secrets injection behind the four-state `secret:<NAME>` grants. There is
deliberately NO approval dial: unlike a shared library util, a procedure's blast radius
is the routine's own permissions, and the sandbox enforces those regardless of what the
script says.

The header contract is the util docstring standard minus the catalog lines: first line
`<name> — <summary>`, optional `usage:`, `net: outbound|none` (undeclared = none → no
TCP), `secrets:` naming every credential env var read (only declared names are injected;
`NAME?` marks optional — withheld, never prompted, when not granted). There is no
`calls:` graph and `gu` is NOT on PATH: a step that needs a util's capability belongs in
the recipe, not inside a procedure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import sandbox, utils_lib
from .ids import is_slug

PROC_TIMEOUT_S = 300


def procedures_dir(routine_dir: Path) -> Path:
    return routine_dir / "procedures"


def script_path(routine_dir: Path, name: str) -> Path:
    return procedures_dir(routine_dir) / f"{name}.py"


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


def run_procedure(routine_dir: Path, name: str, args: list[str], *,
                  policy: sandbox.SandboxPolicy, libraries_home: Path,
                  timeout: int = PROC_TIMEOUT_S,
                  extra_secrets: dict[str, str] | None = None,
                  withhold_secrets: set[str] | None = None) -> tuple[int, str, str]:
    """Controlled runner, the util runner's private-sibling twin: scoped env (declared
    secrets only, minus withheld optionals), the run-scoped Landlock jail + the script's
    own `net:` line, working directory = the routine dir (relative paths resolve like
    read_file/write_file). Returns (exit, out, err).
    """
    if not exists(routine_dir, name):
        have = ", ".join(p["name"] for p in list_procedures(routine_dir)) or "(none yet)"
        return 2, "", f"no procedure {name!r} (available: {have})"
    declared, net, _opt = needs(routine_dir, name)
    env = utils_lib.scoped_env(declared, extra_secrets, withhold_secrets)
    script = str(script_path(routine_dir, name))
    if not net:   # same build-time dependency phase a net:none util gets (R40)
        utils_lib.prewarm_script_deps(script, policy, libraries_home)
    try:
        cmd = sandbox.wrap(["uv", "run", script, *[str(a) for a in args]],
                           policy=policy, libraries_home=libraries_home, net=net)
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
