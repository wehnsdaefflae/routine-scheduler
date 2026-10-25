"""The per-routine script INTERPRETER: a dependency-free script needs no venv at all, and the
venv the others need lives where the jailed subprocess can actually create it (F764).

R2887 / R2891 / R2892, 2026-10-24: `script` had never once worked for a routine without a
hand-made venv —

    error: Failed to create virtual environment
      Caused by: failed to create directory `/home/mark/routines/<routine>/.venv`:
      Permission denied (os error 13)

— so `scheduler-improvement-research/scripts/outcome.py`, the script that counts what that
routine produces, had never run, and the conversation surface advertised `script` in
CAPABILITIES and then failed on it.

It was never a missing GRANT: the engine's own `write_file` creates `scripts/months.py` in that
same directory happily. D179's own-dir seal mounts the routine dir READ-ONLY and its unsealed
children read-write (`ownseal.mount_own_dir`), and a Landlock rule attaches to an inode — so a
child that does not exist yet cannot be mounted, and a subprocess therefore cannot create a NEW
top-level entry inside the routine dir at all. `ownseal.writable_children` says exactly that in
prose ("a command creating a NEW top-level entry inside the routine dir meets a bare kernel
error"), and `.venv` was such an entry. Widening a grant would not have fixed it.

Both halves of the fix are tested here: a script whose PEP 723 block declares no dependencies
runs on the interpreter the daemon runs — the precedent `daemon/gate_prepare.py` already sets
for an `admit.py` without deps — so the venv that was failing is not even attempted; and the
venv the dependency-carrying scripts do need lives inside `scripts/`, a directory that EXISTS
whenever a script does, is neither engine-sealed nor recipe-sealed, and is mounted read-write
for every routine.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from rsched import ownseal, sandbox, scripts, utils_run

NO_DEPS = '''# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""counter — a dependency-free script, the shape of every scripts/outcome.py (R2887).

net: none
fs: roots
"""
import json
import sys

print(json.dumps({"executable": sys.executable, "args": sys.argv[1:]}))
'''

WITH_DEPS = (NO_DEPS.replace("# dependencies = []", '# dependencies = ["idna"]')
             .replace("counter —", "fetcher —"))


def _home(tmp_path: Path, script: str = NO_DEPS, name: str = "counter") -> Path:
    d = tmp_path / "a-routine"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / f"{name}.py").write_text(script, encoding="utf-8")
    (tmp_path / "lib").mkdir(exist_ok=True)
    return d


def _writable(rw: list[str], path: Path) -> bool:
    """Is `path` writable under this mount — i.e. is any rw rule it or an ancestor of it?

    Landlock rights are INHERITED down a hierarchy, so literal set membership is the wrong
    question and asserting it is how a mount test passes on code that mounts the parent
    (the lesson `test_own_dir_seal._writable` carries).
    """
    return any(path == Path(r) or path.is_relative_to(Path(r)) for r in rw)


def test_a_dependency_free_script_runs_on_the_system_interpreter(tmp_path):
    """R2887's live specimen declares `dependencies = []` and imports json/re/sys/pathlib
    only: the venv it died creating would have had nothing installed into it. No venv is
    built, and nothing is written to the routine's `.gitignore` to cover one.
    """
    d = _home(tmp_path)
    code, out, err = scripts.run_script(
        d, "counter", ["x"], policy=sandbox.SandboxPolicy(mode="off"),
        libraries_home=tmp_path / "lib")
    assert code == 0, err
    assert json.loads(out) == {"executable": sys.executable, "args": ["x"]}
    assert not list(d.glob("**/.venv")), "a venv was built for a script declaring no deps"
    assert not (d / ".gitignore").exists(), "nothing to ignore, so nothing is written"


def test_the_venv_a_dependency_carrying_script_needs_is_mounted_writable(tmp_path):
    """The placement test, in the terms R2892's diagnosis is in: can the venv build create
    the directory at all? `.venv` at the routine-dir ROOT cannot — the dir itself is mounted
    unwritable (list-only since D207) and there is no rule for a child that does not exist —
    while `scripts/` is an
    ordinary unsealed child, mounted read-write for every routine.
    """
    from rsched import scriptenv

    d = _home(tmp_path, WITH_DEPS, "fetcher")
    ro: list[str] = []
    rw: list[str] = []
    ls: list[str] = []
    ownseal.mount_own_dir(sandbox.SandboxPolicy(own_dir=d), ro, rw, ls)
    assert _writable(rw, scriptenv.venv_dir(d)), "the script venv is unwritable in the jail"
    assert _writable(rw, scriptenv.venv_python(d))
    assert not _writable(rw, d / ".venv"), "the pre-F764 placement reads as writable"
    # the dir stays VISIBLE and unwritable — F764's whole cause. Since D207 it is visible as a
    # LIST-ONLY mount rather than a read-only one (the run-history depth needs that shape:
    # `rundepth`), which changes nothing about the write placement this test is about.
    assert str(d) in ls and str(d) not in ro


def test_the_kernel_refuses_the_old_placement_and_allows_the_new(tmp_path, monkeypatch):
    """The one test here that is evidence rather than arithmetic: R2892's hand probe
    (`gu fs-ops mkdir <routine-dir>/venvtest` → Errno 13) as a test, in a REAL Landlock jail.

    The toolchain's tmp rules are withheld for the duration, exactly as
    `test_own_dir_seal.py` does it: `sandbox._toolchain` mounts `/tmp` read-WRITE for every
    jailed command (uv and bash need a scratch dir) and pytest's `tmp_path` lives under it —
    so without this, the routine dir is writable by the TOOLCHAIN rule whatever the own-dir
    mount says, and this test would go green at exit 0 against the very placement that fails
    in production.
    """
    from rsched import scriptenv

    if not sandbox.available():
        pytest.skip("Landlock unavailable on this kernel — the jail cannot engage")
    real_toolchain = sandbox._toolchain
    scratch = Path(tempfile.gettempdir()).resolve()

    def _tmp_read_only():
        ro, rw = real_toolchain()
        moved = [r for r in rw if Path(r).resolve() == scratch
                 or scratch.is_relative_to(Path(r).resolve())]
        return [*ro, *moved], [r for r in rw if r not in moved]

    monkeypatch.setattr(sandbox, "_toolchain", _tmp_read_only)
    d = _home(tmp_path, WITH_DEPS, "fetcher")
    policy = sandbox.SandboxPolicy(mode="strict", own_dir=d)

    def _jailed_mkdir(target: Path) -> int:
        cmd = sandbox.wrap([sys.executable, "-c", f"import os; os.mkdir({str(target)!r})"],
                           policy=policy, libraries_home=tmp_path / "lib",
                           net=True, fs_roots=False, fs_paths=())
        return utils_run.run_jailed(cmd, env=dict(os.environ), cwd=d, timeout=60).exit_code

    assert _jailed_mkdir(d / ".venv") != 0, "the jail allowed the write R2892 measured as 13"
    assert not (d / ".venv").exists()
    assert _jailed_mkdir(scriptenv.venv_dir(d)) == 0, "the venv's own directory is unwritable"
    assert scriptenv.venv_dir(d).is_dir()
