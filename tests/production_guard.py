"""The sandbox floor under the whole suite: no test may touch the LIVE instance's data."""

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
    import os

    from rsched.config import load_server_config
    from rsched.paths import config_file, expand
    from rsched.registry import all_homes

    server, _ = load_server_config()
    state_home = Path(os.environ.get("XDG_STATE_HOME") or expand("~/.local/state"))
    paths = {*all_homes(server), server.libraries_home, config_file().parent,
             expand("~/.credentials"), state_home / "routine-scheduler"}
    return tuple(sorted(p for p in paths if len(p.parts) > 2))   # never a root-ish path


_PROTECTED = _instance_paths()
_EXACT = frozenset(str(p) for p in _PROTECTED)
_PREFIXES = tuple(f"{p}{os.sep}" for p in _PROTECTED)
_WRITE_MODES = frozenset("wxa+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _absolute(target: object, base: str | None = None) -> str | None:
    """The absolute, lexically normalised path `target` names — None for an already-open fd
    (its path was checked when it opened) or for anything that is not a path. A relative path
    resolves against `base` when the call names one (`_fd_dir`, a symlink's own directory),
    against the cwd otherwise.
    """
    if isinstance(target, int):
        return None
    try:
        raw = os.fspath(target)   # type: ignore[arg-type]
    except TypeError:
        return None
    path = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if path.startswith(os.sep):
        return os.path.normpath(path)
    if base is None:
        return os.path.abspath(path)  # noqa: PTH100 — see the docstring: a string op, no stat
    return os.path.normpath(f"{base}{os.sep}{path}")


def _fd_dir(dir_fd: int | None) -> str | None:
    """The directory a call's `dir_fd` keyword names. `shutil.rmtree` deletes through
    `unlink(name, dir_fd=…)`: read against the cwd every bare name looked harmless, so it could
    empty a home of everything but the top directory — the one call it makes with a full path.
    """
    return None if dir_fd is None else str(Path(f"/proc/self/fd/{dir_fd}").readlink())


def _check(op: str, target: object, base: str | None = None) -> None:
    path = _absolute(target, base)
    if path is not None and (path in _EXACT or path.startswith(_PREFIXES)):
        raise ProductionWriteError(
            f"{op}: {path} is inside the LIVE instance's data. A test must never write "
            "there — point the code under test at tmp_path (the `_hermetic_home` fixture "
            "does this for `~`), or stub the write.")


#: Programs that run ANOTHER program named further along their argv. `uv run rsched …` is how
#: README, CLAUDE.md and the Dockerfile's own CMD start the CLI — the launcher resolves the
#: console script, so argv[0] is `uv`, never `rsched`.
_LAUNCHERS = frozenset({"uv", "uvx", "env"})


def _runs_the_cli(parts: object) -> bool:
    """True when this argv starts THIS package's CLI: `rsched …`, `python -m rsched…`, or a
    launcher (`uv run [options] rsched …`, `uvx rsched`, `env VAR=… rsched`) naming it.
    `uv run --script` runs a util, whose own arguments may say anything."""
    if isinstance(parts, (str, bytes, os.PathLike)):
        parts = str(os.fspath(parts) if isinstance(parts, os.PathLike) else parts).split()
    try:
        argv = [str(p) for p in parts]   # type: ignore[union-attr]
    except TypeError:
        return False
    if not argv:
        return False
    if Path(argv[0]).name == "rsched":
        return True
    if Path(argv[0]).name in _LAUNCHERS and "--script" not in argv and "rsched" in argv[1:]:
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
            _check("os.open", path, _fd_dir(kwargs.get("dir_fd")))
        return real_os_open(path, flags, *args, **kwargs)

    mp.setattr(builtins, "open", guarded_open)
    mp.setattr(io, "open", guarded_io_open)
    mp.setattr(os, "open", guarded_os_open)
    for name in ("replace", "rename", "link"):
        real = getattr(os, name)
        def guarded_pair(src, dst, *args, _real=real, _op=name, **kwargs):
            _check(f"os.{_op}", src, _fd_dir(kwargs.get("src_dir_fd")))
            _check(f"os.{_op}", dst, _fd_dir(kwargs.get("dst_dir_fd")))
            return _real(src, dst, *args, **kwargs)
        mp.setattr(os, name, guarded_pair)
    real_symlink = os.symlink

    def guarded_symlink(src, dst, *args, **kwargs):
        # `src` is the link's TARGET: a link into a home is a write route that a later write
        # through it hides from the string check, so making one is refused — and a relative
        # target resolves against the link's own directory, never the cwd
        link = _absolute(dst, _fd_dir(kwargs.get("dir_fd")))
        _check("os.symlink", link)
        _check("os.symlink", src, str(Path(link).parent) if link else None)
        return real_symlink(src, dst, *args, **kwargs)

    mp.setattr(os, "symlink", guarded_symlink)
    for name in ("mkdir", "rmdir", "remove", "unlink", "truncate", "chmod"):
        real = getattr(os, name)
        def guarded_one(path, *args, _real=real, _op=name, **kwargs):
            _check(f"os.{_op}", path, _fd_dir(kwargs.get("dir_fd")))
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
