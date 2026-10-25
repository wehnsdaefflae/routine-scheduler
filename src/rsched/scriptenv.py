"""WHERE a per-routine script's INTERPRETER comes from — and why it is usually not a venv.

Two rules, and the second only applies when the first does not:

1. A script whose PEP 723 block declares NO dependencies runs on the interpreter the daemon
   itself runs (`sys.executable`). There is nothing to install, so there is nothing to build —
   the precedent `daemon/gate_prepare.py` already set for an `admit.py` without dependencies.
2. A script that DOES declare dependencies gets a persistent venv at `<routine>/scripts/.venv`,
   its deps installed into it on first use (a net-open build step, R40's prewarm rationale),
   gitignored against the run-end `git add -A`.

Both rules exist because of one defect (F764, from R2887 + R2891 + R2892, 2026-10-24): `script`
had never once worked for a routine that did not already have a hand-made venv —

    error: Failed to create virtual environment
      Caused by: failed to create directory `/home/mark/routines/<routine>/.venv`:
      Permission denied (os error 13)

— so `scheduler-improvement-research/scripts/outcome.py`, the script that counts what that
routine produces, had never run in its life, and the conversation surface advertised `script`
in CAPABILITIES and then failed on it.

It was never a missing GRANT, which is what makes the placement the whole fix: the engine's own
`write_file` creates `scripts/<name>.py` in that same directory happily. D179's own-dir seal
mounts the routine dir READ-ONLY and its unsealed children read-write (`ownseal.mount_own_dir`),
because Landlock has no deny rule and a read-only hole cannot be carved inside a writable mount.
A Landlock rule attaches to an INODE, so a child that does not exist yet cannot be mounted at
all — `ownseal.writable_children` says exactly that in prose: "a command creating a NEW
top-level entry inside the routine dir meets a bare kernel error". `.venv` was such an entry.
Widening a grant would not have fixed it and would have papered over it.

`scripts/` is the right home for three reasons, each checked rather than assumed: it EXISTS
whenever a script does (the script file is in it), it is neither engine-sealed nor recipe-sealed
(`ownseal.SEALED_DIRS`, `grants.RECIPE_PREFIXES`), and it therefore carries a read-write mount
for EVERY routine — not only one that happens to hold its own dir as an fs write root. Probed
from inside a real script jail, 2026-10-25: `mkdir <routine>/scripts/<x>` succeeds,
`mkdir <routine>/<x>` is the Errno 13 above wherever the routine dir is not also a granted write
root. `/tmp` was rejected as a candidate: the toolchain mounts it read-write for every jailed
command, so a venv there would be reachable by every other command in the run and no seal test
over it could mean anything.

THE ACCEPTED COST of rule 1: the daemon's interpreter carries the daemon's own site-packages, so
a dep-free script that imports a third-party module may now succeed where the old empty venv
would have failed on the import. Declaring the dependency is still the only way to PIN one —
an undeclared import rides on whatever the daemon's environment happens to have. The
alternative (an empty venv nobody can create) fixed nothing. The interpreter is invoked plainly,
without `-I`/`-E`, so `sys.path[0]` remains the script's own directory exactly as the venv
python left it: a script that imports a sibling module out of `scripts/` keeps working.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from . import sandbox, utils_run
from .paths import ensure_gitignored

#: The venv's path RELATIVE TO THE ROUTINE DIR — inside `scripts/`, never beside it. Also the
#: `.gitignore` entry, so the two cannot drift apart.
VENV_DIR = "scripts/.venv"
_INSTALL_TIMEOUT_S = 300


def venv_dir(routine_dir: Path) -> Path:
    return routine_dir / VENV_DIR


def venv_python(routine_dir: Path) -> Path:
    return venv_dir(routine_dir) / "bin" / "python"


def script_deps(routine_dir: Path, name: str) -> list[str]:
    """The script's PEP 723 `dependencies`, from its `# /// script` block. [] when the
    block is absent or unparseable — a missing dep then fails visibly at exec.
    """
    try:
        text = (routine_dir / "scripts" / f"{name}.py").read_text(encoding="utf-8")
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


def interpreter(routine_dir: Path, name: str) -> Path:
    """What runs this script: the daemon's own interpreter when it declares no dependencies,
    its routine's venv python when it declares any (rules 1 and 2 of the module docstring).

    Asked AFTER `ensure_env`, which is what makes the venv exist in the second case.
    """
    return venv_python(routine_dir) if script_deps(routine_dir, name) else Path(sys.executable)


def ensure_env(routine_dir: Path, name: str, *,
               policy: sandbox.SandboxPolicy, libraries_home: Path,
               fs_paths: tuple[tuple[str, str], ...] = (),
               aborted: Callable[[], bool] | None = None) -> str | None:
    """Create `<routine>/scripts/.venv` if missing and install this script's PEP 723
    dependencies into it (a fast no-op when already satisfied). Returns an error line, or None.

    A script declaring NO dependencies returns at once, having built nothing: that is rule 1,
    and the single most common case — every `scripts/outcome.py` in the instance is one.

    The install is a BUILD step: it runs net-open inside the routine jail (the util prewarm's
    R40 rationale — a `net: none` script must still be able to fetch its deps), and a failure
    surfaces instead of letting the exec die on an import it cannot explain. A build step ends
    with its run like the script it builds for (`aborted`, `utils_run.run_jailed`), and the line
    it returns is then the runner's own note. `fs_paths` are the private stores the script's
    declared utils claim (`scripts.callee_fs_paths`), passed in rather than resolved here so
    this module stays free of the header layer.
    """
    deps = script_deps(routine_dir, name)
    if not deps:
        return None
    # git add -A would otherwise commit the whole interpreter tree into the routine's repo
    ensure_gitignored(routine_dir, VENV_DIR, "the scripts venv (rebuilt on demand)")
    py = venv_python(routine_dir)
    steps: list[list[str]] = []
    if not py.exists():
        steps.append(["uv", "venv", str(venv_dir(routine_dir))])
    steps.append(["uv", "pip", "install", "--quiet", "--python", str(py), *deps])
    for step in steps:
        try:
            cmd = sandbox.wrap(step, policy=policy, libraries_home=libraries_home,
                               fs_roots=True, fs_paths=fs_paths, net=True)
        except sandbox.SandboxRefusal as exc:
            return str(exc)
        r = utils_run.run_jailed(cmd, env=dict(os.environ), cwd=routine_dir,
                                 timeout=_INSTALL_TIMEOUT_S, label=f"venv setup ({step[1]})",
                                 aborted=aborted)
        if r.aborted:
            return r.stderr.strip()
        if r.timed_out:
            # The install cap is fixed and SEPARATE from the action's timeout_s, which bounds the
            # script's runtime, not this build step — so no recipe can raise it (R1296). A dep too
            # heavy to install inside it does not belong in a per-routine venv; say so, rather than
            # leave the caller re-setting timeout_s to no effect.
            return (f"venv setup failed ({step[1]}): dependency install exceeded the fixed "
                    f"{_INSTALL_TIMEOUT_S}s cap. That cap is not the action's timeout_s (which "
                    "bounds the script's runtime, not this build) and cannot be raised from a "
                    "recipe — use a lighter dependency, or offload heavy compute to a util or a "
                    "remote machine.")
        if r.returncode != 0:
            return (f"venv setup failed ({step[1]}): "
                    f"{(r.stderr or r.stdout).strip()[-400:]}")
    return None
