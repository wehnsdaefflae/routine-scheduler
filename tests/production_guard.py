"""The sandbox floor under the whole suite: no test may touch the LIVE instance's data.

`conftest._hermetic_home` redirects `~` for `rsched.config`, but that binds this process
only. A test that spawns a subprocess escapes it completely — and in F394 (2026-08-27) one
did: a stub patch stopped taking after a refactor moved the symbol it patched, the daemon's
Runner spawned the REAL `rsched.cli engine-run`, and that fresh interpreter loaded
`~/.config/routine-scheduler/config.yaml` and executed a tmp-homed fixture routine against
production — eleven turns on a paid endpoint, two rows in the live report ledger, for a
routine that has never existed on that instance. The engine spawn itself now refuses a
config nobody pointed it at (`daemon.runner_state.engine_cmd`); this module is the wider
net under the whole class, so the NEXT escape route fails on its first write instead.

Two rules, one session-scoped autouse fixture (session, not function: the process-wide
chokepoints are patched once and the cost is a string compare per write, where re-patching
per test would buy nothing and pay 1900×):

1. No write lands inside one of the instance's real data homes. Every Python write
   chokepoint this codebase reaches is covered — `open`/`io.open`, the `mkstemp` + `replace`
   pair behind `paths.atomic_write`, and the mkdir/unlink/rmdir/symlink family that
   `Path` and `shutil` delegate to.
2. No test runs this package's CLI as a subprocess. That child would load the production
   config by definition — it is a fresh interpreter with none of the test's redirections —
   so the spawn is refused before it exists, whatever subcommand it names.

The rule is "the instance's data homes", NOT "anything outside tmp_path": tests legitimately
write to /tmp, to the checkout (`__pycache__`, `.pytest_cache`), and — through the real
`uv run` that the util tests exercise — to the real `~/.cache` and `~/.local/share/uv`.
Those are caches a machine rebuilds. These are the paths where a stray write costs money,
rewrites a real routine's history, or leaks into a ledger nobody can un-append.
"""

from __future__ import annotations

import builtins
import io
import os
import subprocess
from pathlib import Path

import pytest


class ProductionWriteError(BaseException):
    """A test reached into the live instance. Derived from BaseException on purpose: the
    code under test is full of `except OSError` / `except Exception` fallbacks that would
    otherwise swallow this and let the write look like a tolerated failure.
    """


def _instance_paths() -> tuple[Path, ...]:
    """The live instance's data dirs, read from the host's own config at import time —
    before any fixture redirects `~`, so these are the REAL ones even under the hermetic
    home. The homes come from `registry.all_homes` rather than an inline list, so a fourth
    home can never be added to the system and forgotten here.
    """
    from rsched.config import load_server_config
    from rsched.paths import config_file, expand
    from rsched.registry import all_homes

    server, _ = load_server_config()
    paths = {*all_homes(server), server.libraries_home, config_file().parent,
             expand("~/.credentials")}
    return tuple(sorted(p for p in paths if len(p.parts) > 2))   # never a root-ish path


_PROTECTED = _instance_paths()
_EXACT = frozenset(str(p) for p in _PROTECTED)
_PREFIXES = tuple(f"{p}{os.sep}" for p in _PROTECTED)
_WRITE_MODES = frozenset("wxa+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _check(op: str, target: object) -> None:
    if isinstance(target, int):   # an already-open fd — the path was checked when it opened
        return
    try:
        raw = os.fspath(target)   # type: ignore[arg-type]
    except TypeError:
        return
    path = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if not path.startswith(os.sep):
        # abspath, not resolve(): this runs on EVERY write in the process, and resolve()
        # would stat the filesystem each time (and follow symlinks out of the check).
        path = os.path.abspath(path)  # noqa: PTH100
    if path in _EXACT or path.startswith(_PREFIXES):
        raise ProductionWriteError(
            f"{op}: {path} is inside the LIVE instance's data. A test must never write "
            "there — point the code under test at tmp_path (the `_hermetic_home` fixture "
            "does this for `~`), or stub the write.")


def _runs_the_cli(parts: object) -> bool:
    """True when this argv starts THIS package's CLI: `rsched …` or `python -m rsched…`."""
    if isinstance(parts, (str, bytes, os.PathLike)):
        parts = str(os.fspath(parts) if isinstance(parts, os.PathLike) else parts).split()
    try:
        argv = [str(p) for p in parts]   # type: ignore[union-attr]
    except TypeError:
        return False
    if argv and Path(argv[0]).name == "rsched":
        return True
    return any(argv[i] == "-m" and argv[i + 1].split(".")[0] == "rsched"
               for i in range(len(argv) - 1))


def _refuse_cli(argv: object) -> None:
    if _runs_the_cli(argv):
        raise ProductionWriteError(
            f"spawning {argv!r} runs this package's CLI as a subprocess: a fresh "
            "interpreter that inherits none of this test's redirections and loads the "
            "PRODUCTION config. Call the function under test directly, or stub the spawn.")


@pytest.fixture(autouse=True, scope="session")
def _no_production_writes():
    """Install both rules for the whole session (see this module's docstring)."""
    mp = pytest.MonkeyPatch()
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODES & set(str(mode)):
            _check("open", file)
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODES & set(str(mode)):
            _check("io.open", file)
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS:
            _check("os.open", path)
        return real_os_open(path, flags, *args, **kwargs)

    mp.setattr(builtins, "open", guarded_open)
    mp.setattr(io, "open", guarded_io_open)
    mp.setattr(os, "open", guarded_os_open)
    for name in ("replace", "rename", "link", "symlink"):
        real = getattr(os, name)
        def guarded_pair(src, dst, *args, _real=real, _op=name, **kwargs):
            _check(f"os.{_op}", src)
            _check(f"os.{_op}", dst)
            return _real(src, dst, *args, **kwargs)
        mp.setattr(os, name, guarded_pair)
    for name in ("mkdir", "rmdir", "remove", "unlink", "truncate", "chmod"):
        real = getattr(os, name)
        def guarded_one(path, *args, _real=real, _op=name, **kwargs):
            _check(f"os.{_op}", path)
            return _real(path, *args, **kwargs)
        mp.setattr(os, name, guarded_one)

    real_popen = subprocess.Popen.__init__

    def guarded_popen(self, args, *rest, **kwargs):
        _refuse_cli(args)
        return real_popen(self, args, *rest, **kwargs)

    mp.setattr(subprocess.Popen, "__init__", guarded_popen)

    import asyncio
    real_exec, real_shell = asyncio.create_subprocess_exec, asyncio.create_subprocess_shell

    async def guarded_exec(program, *args, **kwargs):
        _refuse_cli([program, *args])
        return await real_exec(program, *args, **kwargs)

    async def guarded_shell(cmd, **kwargs):
        _refuse_cli(cmd)
        return await real_shell(cmd, **kwargs)

    mp.setattr(asyncio, "create_subprocess_exec", guarded_exec)
    mp.setattr(asyncio, "create_subprocess_shell", guarded_shell)
    yield
    mp.undo()
