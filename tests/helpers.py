"""Builders the fast suite's files used to carry a copy of each.

A server over tmp homes, a RunContext over a scaffolded routine, a routine's capabilities
rewritten by hand: each was copied into every file that needed one, so a constructor or a
layout that changed had to be found and answered in every copy. These are plain functions —
none holds state or needs a teardown; the fixtures that do live in conftest.py, beside the
config and client builders they share a home with. A helper with one caller stays in its test
file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy

# ---- servers ----------------------------------------------------------------------------


def server_config(**attrs: object) -> ServerConfig:
    """`ServerConfig()` with `attrs` assigned after construction — the way every hand-built
    test server is made. A home it does not name stays the hermetic default
    (conftest.py's `_hermetic_home`)."""
    server = ServerConfig()
    for name, value in attrs.items():
        setattr(server, name, value)
    return server


def server_for(routine_dir: Path, **attrs: object) -> ServerConfig:
    """The server a routine made by `make_routine` runs under: its parent is the routines home,
    so `.control` logs land in tmp, and the library is a sibling `test-library` that holds only
    what a test puts there."""
    return server_config(**{"routines_home": routine_dir.parent,
                            "libraries_home": routine_dir.parent.parent / "test-library",
                            **attrs})


def tmp_server(tmp_path: Path, *, create: bool = True, **attrs: object) -> ServerConfig:
    """A server whose routines home is `tmp_path/routines`, created unless `create=False`."""
    server = server_config(routines_home=tmp_path / "routines", **attrs)
    if create:
        server.routines_home.mkdir(parents=True, exist_ok=True)
    return server


def stats_server(tmp_path: Path) -> ServerConfig:
    """Tmp routines, conversations and library homes, with the `.control` dir the run-usage
    stream lands in already there."""
    server = server_config(routines_home=tmp_path / "routines",
                           conversations_home=tmp_path / "conversations",
                           libraries_home=tmp_path / "library")
    (server.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    return server


# ---- a run's context ----------------------------------------------------------------------


def run_context(routine_dir: Path, ts: str, *, server: ServerConfig | None = None,
                registry: object = None, **fields: object) -> RunContext:
    """A real root RunContext over a scaffolded routine dir: the routine's own config,
    `runs/<ts>/` with its transcript, the budgets the routine declares. `fields` are further
    RunContext fields (`grants`, `turn`, `depth`, …)."""
    cfg, _problems = load_routine(routine_dir)
    assert cfg is not None
    run_dir = routine_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(routine=cfg, server=server if server is not None else ServerConfig(),
                      registry=registry, run_ts=ts, run_dir=run_dir,
                      transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets), **fields)


def action_ctx(routine_dir: Path, tmp_path: Path, ts: str = "20260716-070000") -> RunContext:
    """The RunContext the file-action tests dispatch against: an empty library under
    `tmp_path`, and an empty GrantPolicy where a live run holds its own."""
    return run_context(routine_dir, ts, grants=GrantPolicy(),
                       server=server_config(libraries_home=tmp_path / "libraries"))


def set_capabilities(routine_dir: Path, **caps: object) -> None:
    """Merge `caps` into the routine.yaml `capabilities:` mapping, as a hand edit would."""
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["capabilities"] = {**(raw.get("capabilities") or {}), **caps}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
