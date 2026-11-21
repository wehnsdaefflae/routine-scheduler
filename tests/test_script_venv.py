"""The per-routine script INTERPRETER: a dependency-free script needs no venv at all, and the
venv the others need lives where the jailed subprocess can actually create it (F764).
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
    """Is `path` writable under this mount — i.e. is any rw rule it or an ancestor of it?"""
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
