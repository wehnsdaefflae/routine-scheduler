"""Per-routine SCRIPTS — the routine's own persistent helper tooling.

A script is ONE PEP 723 file `scripts/<name>.py` inside the routine's own dir — private
to the routine, versioned by its repo's autocommit, authored by the run itself
(`write_file`) or by the user — executed in a persistent venv in the routine's workdir
(`<routine>/.venv`, deps installed on first use, gitignored). It is the recipe's TOOLING,
never its peer: the recipe stays the single interpreter of the task and delegates to a
script only sub-steps that need no judgment (polling, parsing, calculations, assembling a
fixed artifact). A repeating deterministic sub-step is written into `scripts/` once and
called thereafter — reproducible, no model work.

The envelope is deliberately NARROWER than a util call's:

- FILESYSTEM: the jail is the run's own fs roots (`sandbox.wrap`, the same policy the
  recipe's file actions honor) — recipe and script read and write the SAME files.
- SECRETS: declared-only, exactly the util model. The header `secrets:` line names every
  credential env var the script reads; only DECLARED **and granted** names are injected
  (`NAME?` marks an optional one, withheld rather than prompted when not granted), and a
  declared, present, still-undecided secret files the same blocking exposure ask a util
  call would (engine/secretgate.gate_script_secrets). Everything else in the store is scrubbed.
- UTILS: declared-only, exactly the sibling rule utils themselves follow. A `calls:`
  header line puts the `gu` dispatcher on PATH and folds every named util's `secrets:`
  and `net:` into THIS script's env and jail (`utils_run.util_needs` — one call tree, one
  jail, one env). A script declaring no calls gets no library handle at all; one that
  execs an undeclared sibling is refused, not silently under-granted.
- NO MODEL ACCESS: there is no LLM channel — a judgment call belongs in the recipe.
- ASKS: mid-run escalation (`ask_user`, blocking approvals) is the recipe's channel — a
  script gets its declared grants; anything more is requested recipe-side.

The `script` action is GATED by the `script` capability (the `scripts` permission doc).
There is deliberately NO approval dial: a script's blast radius is a subset of the
routine's own permissions, and the sandbox enforces those regardless of the code.
Header contract: first line `<name> — <summary>`, optional `usage:`, `net: outbound|none`
(undeclared = none → no TCP at exec; the dependency install is a net-open build step,
R40), `secrets:` for the exposure gate above, `calls:` for the utils it shells out to.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from . import sandbox, utils_header, utils_lib, utils_run
from .paths import atomic_write

SCRIPT_TIMEOUT_S = 300
VENV_DIR = ".venv"
_INSTALL_TIMEOUT_S = 300


def scripts_dir(routine_dir: Path) -> Path:
    return routine_dir / "scripts"


def script_path(routine_dir: Path, name: str) -> Path:
    return scripts_dir(routine_dir) / f"{name}.py"


def venv_dir(routine_dir: Path) -> Path:
    return routine_dir / VENV_DIR


def venv_python(routine_dir: Path) -> Path:
    return venv_dir(routine_dir) / "bin" / "python"


# A script's name is the ROUTINE'S own choice: kebab-case like a util, or the snake_case a
# Python author writes reflexively. The old kebab-only (ids.is_slug) check made
# `scripts/gen_random_strings.py` unreachable while list_scripts still ADVERTISED its stem —
# "does not exist. Available: gen_random_strings" — and the miss message then taught
# re-writing that very filename: an infinite loop two conversations actually ran
# (R336/R337). Dots and path separators stay rejected — the name is interpolated into
# `scripts/<name>.py`.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def exists(routine_dir: Path, name: str) -> bool:
    return bool(NAME_RE.match(name)) and script_path(routine_dir, name).is_file()


def list_scripts(routine_dir: Path) -> list[dict]:
    """The routine's own script catalog: {name, summary, usage} per script, from the
    docstring header. A script whose header cannot be parsed still lists (empty summary)
    — discoverability must not depend on hygiene.
    """
    d = scripts_dir(routine_dir)
    out: list[dict] = []
    for p in sorted(d.glob("*.py")) if d.is_dir() else []:
        try:
            header = utils_header.parse_header(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # a broken script must not hide its siblings
            header = {"summary": "", "usage": ""}
        out.append({"name": p.stem, "summary": header.get("summary", ""),
                    "usage": header.get("usage", "")})
    return out


def _header(routine_dir: Path, name: str) -> dict | None:
    try:
        return utils_header.parse_header(
            script_path(routine_dir, name).read_text(encoding="utf-8"))
    except OSError:
        return None


def declared_calls(routine_dir: Path, name: str) -> list[str]:
    """The library utils this script's `calls:` line names — what earns it `gu` on PATH."""
    header = _header(routine_dir, name)
    return list(header["calls"]) if header else []


def needs(routine_dir: Path, name: str,
          libraries_home: Path) -> tuple[set[str], bool, set[str]]:
    """(declared secret env vars, net-outbound?, the OPTIONAL subset) for one script,
    resolved TRANSITIVELY across the `calls:` utils its header declares — the script and
    every util it shells out to share ONE jail and ONE env, the same rule a util's own
    siblings obey (`utils_run.util_needs`, which walks each callee's subtree). So a script
    calling a `net: outbound` util needs the network open too, and inherits that util's
    credentials without redeclaring them. A name is OPTIONAL only if every declarer in the
    tree marks it `?` — one required declaration anywhere makes it required.
    """
    header = _header(routine_dir, name)
    if header is None:
        return set(), False, set()
    declared = {s.upper() for s in header["secrets"]}
    required = declared - {s.upper() for s in header["optional_secrets"]}
    net = header["net"] == "outbound"
    for callee in header["calls"]:
        c_secrets, c_net, c_optional = utils_run.util_needs(libraries_home, callee)
        declared |= c_secrets
        required |= c_secrets - c_optional
        net = net or c_net
    return declared, net, declared - required


def call_problems(routine_dir: Path, name: str, libraries_home: Path) -> list[str]:
    """The two ways a script's util access is declared wrong, both leaving it to run
    WITHOUT the access it needs: it execs `gu <util>` the `calls:` line never names (so
    that util's secrets and net never reach the shared jail), or it declares a util this
    library does not have (so the declaration resolves to nothing). Refused at the
    declaration — the same bargain `misdeclared` strikes, since failing loudly here beats
    failing obscurely at the first env read or blocked socket.
    """
    header = _header(routine_dir, name)
    if header is None:
        return []
    src = script_path(routine_dir, name).read_text(encoding="utf-8")
    declared = set(header["calls"])
    problems = []
    if undeclared := sorted(set(utils_header.GU_CALL_RE.findall(src)) - declared):
        problems.append("execs util(s) the docstring's 'calls:' line does not name: "
                        + ", ".join(undeclared))
    if unknown := sorted(c for c in declared
                         if not utils_lib.exists(libraries_home, c)):
        problems.append("declares util(s) the library does not have: " + ", ".join(unknown))
    return problems


_PEP723_BLOCK = re.compile(r"^# /// script\s*$(.*?)^# ///\s*$", re.MULTILINE | re.DOTALL)
_ENGINE_KEYS_IN_BLOCK = re.compile(r"^#\s*(secrets|optional_secrets|net|calls)\s*=", re.MULTILINE)


def misdeclared(routine_dir: Path, name: str) -> list[str]:
    """Engine-header keys an author wrote INSIDE the PEP 723 metadata block (`# secrets =
    [...]`, `# net = "outbound"`), where the engine never reads them. `needs()` parses the
    DOCSTRING header only, so such a declaration silently yields no secrets and no network —
    the script then fails on a missing env var or a blocked socket with no hint at the real
    cause (R444/R419: sprind's publish helper lost FTP_SOURCES *and* HTTPS to exactly this).
    The script action refuses to run such a script and teaches the docstring form instead:
    failing loudly at the declaration beats failing obscurely at the first env read.
    """
    try:
        text = script_path(routine_dir, name).read_text(encoding="utf-8")
    except OSError:
        return []
    m = _PEP723_BLOCK.search(text)
    if not m:
        return []
    return sorted(set(_ENGINE_KEYS_IN_BLOCK.findall(m.group(1))))


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
    atomic_write(gi, "\n".join([*lines, "# the scripts venv (rebuilt on demand)",
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


def run_script(routine_dir: Path, name: str, args: list[str], *,
               policy: sandbox.SandboxPolicy, libraries_home: Path,
               env_secrets: dict[str, str] | None = None,
               timeout: int = SCRIPT_TIMEOUT_S) -> tuple[int, str, str]:
    """Controlled runner: the routine's own venv python on the script, ONLY the caller's
    `env_secrets` injected (the caller filters to declared+granted names; every other
    store key is scrubbed — `utils_run.scoped_env`), the shared jail (`sandbox.wrap` —
    run fs roots), working directory = the routine dir so relative paths resolve like
    read_file/write_file. `gu` is on PATH only for a script that DECLARES the utils it
    calls. Returns (exit, out, err).
    """
    if not exists(routine_dir, name):
        have = ", ".join(p["name"] for p in list_scripts(routine_dir)) or "(none yet)"
        return 2, "", f"no script {name!r} (available: {have})"
    _declared, net, _opt = needs(routine_dir, name, libraries_home)
    if problem := ensure_env(routine_dir, name, policy=policy,
                             libraries_home=libraries_home):
        return 2, "", problem
    env_secrets = dict(env_secrets or {})
    env = utils_run.scoped_env(set(env_secrets), env_secrets)
    # The `calls:` line is what folded the named utils' secrets and net into the env and
    # jail above, so it is also what earns the library handle: declare siblings and `gu`
    # resolves them here (against THIS library, like a util's own sibling calls), declare
    # none and no handle reaches the child at all.
    if declared_calls(routine_dir, name):
        env["PATH"] = f"{libraries_home}:{env.get('PATH', '')}"
        env["GLOBAL_UTILS_HOME"] = str(libraries_home)
    else:
        env.pop("GLOBAL_UTILS_HOME", None)
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
        return 124, "", f"script {name!r} timed out after {timeout}s"
    except OSError as exc:
        return 2, "", f"could not run script {name!r}: {exc}"
    return r.returncode, r.stdout, r.stderr
