"""Where uv may put a MANAGED PYTHON, for every jailed subprocess the instance starts.

uv installs an interpreter it manages into the XDG data dir — `~/.local/share/uv/python`. On
this host (and on any host whose `.local/share` the daemon user does not own) that directory
cannot be created: `/home/<user>/.local/share` is root-owned, so `mkdir` fails before Landlock
is even consulted. `sandbox._HOME_RW` does list `.local/share/uv` as a jail-RW toolchain root,
but `landlock.py` skips a rule path that does not exist — and unlike a routine's GRANTED write
roots, which `sandbox._ensure_write_roots` creates for exactly this reason (R244/F293), the
toolchain list gets no such bootstrap. The effect, reported from inside a run: every
`uv venv --python <version>` dies on "failed to create directory", so no routine can obtain a
second interpreter and nothing in the fleet can be verified against another Python version.

`~/.cache` IS owned by the daemon user and IS a jail-RW toolchain root, and uv creates a missing
install dir itself. So the whole repair is a default: point uv's managed-python root there.
MEASURED on this host before the change — with `UV_PYTHON_INSTALL_DIR=$HOME/.cache/uv/python`,
`uv venv --python 3.10` downloaded CPython 3.10.21 and produced a working interpreter, and a
non-existent directory under `.cache` was created by uv without any daemon-side mkdir.

It is a DEFAULT, never an override: a value already in the environment wins, so the operator (or
a test) can still put the toolchain elsewhere. And it changes nothing that works today — while
the XDG location is unwritable, no managed interpreter has ever been installed there, so nothing
in the fleet can be depending on that path.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The var uv reads for its managed-python root.
PYTHON_INSTALL_DIR = "UV_PYTHON_INSTALL_DIR"


def install_dir(home: Path | None = None) -> str:
    """Where a managed interpreter goes: under `~/.cache/uv`, beside uv's own cache."""
    return str((home or Path.home()) / ".cache" / "uv" / "python")


def with_defaults(env: dict[str, str], home: Path | None = None) -> dict[str, str]:
    """`env` with the toolchain defaults filled in — the caller's own values untouched."""
    out = dict(env)
    if not out.get(PYTHON_INSTALL_DIR):
        out[PYTHON_INSTALL_DIR] = install_dir(home or Path(out.get("HOME") or Path.home()))
    return out


def defaulted_environ(home: Path | None = None) -> dict[str, str]:
    """This process's environment with the defaults filled in — for a step that starts from
    `os.environ` rather than from a scoped env (the per-routine scripts venv build).
    """
    return with_defaults(dict(os.environ), home)
