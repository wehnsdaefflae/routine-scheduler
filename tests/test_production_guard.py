"""The suite's sandbox floor, tested like anything else (see tests/production_guard.py).

A guard nobody exercises is a guard that quietly stops guarding — which is precisely how
F394 happened one layer up, to an engine stub. So the barrier is pointed at a temporary
"instance home" and asked to do its job through the same chokepoints the codebase writes
through: `open`, `paths.atomic_write` (mkstemp + replace), `Path.mkdir`, `Path.unlink`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import production_guard as guard
from rsched.paths import atomic_write


@pytest.fixture
def protected(tmp_path, monkeypatch):
    """Re-point the barrier at a tmp dir standing in for a live instance home."""
    home = tmp_path / "pretend-instance"
    home.mkdir()
    (home / "config.yaml").write_bytes(b"ok\n")   # seeded BEFORE the barrier covers the dir
    monkeypatch.setattr(guard, "_EXACT", frozenset({str(home)}))
    monkeypatch.setattr(guard, "_PREFIXES", (f"{home}{os.sep}",))
    return home


def test_open_for_writing_inside_a_protected_home_is_refused(protected):
    # the builtin `open`, deliberately: the barrier patches it and `io.open` separately,
    # and appending to a ledger is the exact shape of the write F394 got away with
    with pytest.raises(guard.ProductionWriteError) as exc, \
            open(protected / "reports.jsonl", "a", encoding="utf-8") as fh:  # noqa: PTH123
        fh.write("{}\n")
    assert str(protected) in str(exc.value)
    assert not (protected / "reports.jsonl").exists()


def test_reading_is_untouched(protected):
    assert Path(protected / "config.yaml").read_bytes() == b"ok\n"


def test_atomic_write_is_covered(protected):
    """paths.atomic_write never calls open() on its target — it mkstemps beside it and
    renames. Both halves land inside the home, and both are refused.
    """
    with pytest.raises(guard.ProductionWriteError):
        atomic_write(protected / "status.json", "{}")
    assert [p.name for p in protected.iterdir()] == ["config.yaml"]  # no temp file survived


def test_mkdir_and_unlink_are_covered(protected):
    with pytest.raises(guard.ProductionWriteError):
        (protected / "new-routine").mkdir()
    with pytest.raises(guard.ProductionWriteError):
        (protected / "config.yaml").unlink()


def test_writes_outside_a_protected_home_pass(protected, tmp_path):
    atomic_write(tmp_path / "fine.json", "{}")
    assert (tmp_path / "fine.json").read_text(encoding="utf-8") == "{}"


def test_the_protected_set_is_derived_and_covers_the_real_instance():
    """Not an inline list: the paths come from registry.all_homes, so a fourth home added to
    the system is covered without anyone remembering to come back here. The module-level set
    is computed at IMPORT — before any fixture redirects `~` — so it names the real instance
    even though every test runs under a hermetic home.
    """
    from rsched.config import load_server_config
    from rsched.paths import config_file
    from rsched.registry import all_homes

    assert str(config_file().parent) in guard._EXACT          # the real ~/.config dir
    assert all(len(Path(p).parts) > 2 for p in guard._EXACT)  # never a root-ish path
    server, _ = load_server_config()                          # hermetic here, by fixture
    assert {str(h) for h in all_homes(server)} <= {str(p) for p in guard._instance_paths()}


@pytest.mark.parametrize("argv", [
    ["rsched", "engine-run", "x"],
    ["/opt/venv/bin/rsched", "daemon"],
    ["/usr/bin/python3", "-m", "rsched.cli", "engine-run", "x"],
    ["/usr/bin/python3", "-m", "rsched", "abort", "x"],
    "python -m rsched.cli engine-run x",
])
def test_spawning_this_packages_cli_is_refused(argv):
    """Any subcommand, any spelling: that child is a fresh interpreter that would load the
    production config. F394 was exactly this spawn, and nothing said no.
    """
    with pytest.raises(guard.ProductionWriteError) as exc:
        guard._refuse_cli(argv)
    assert "PRODUCTION" in str(exc.value)


@pytest.mark.parametrize("argv", [
    ["bash", "-c", "sleep 0.2"],
    ["uv", "run", "--script", "main.py"],
    ["python3", "-c", "print('rsched')"],
    ["git", "-C", "/srv/checkout", "commit"],
])
def test_the_spawns_tests_really_make_are_left_alone(argv):
    guard._refuse_cli(argv)   # no raise: stub engines, utils, git


async def test_the_barrier_reaches_asyncio_spawns():
    """The F394 spawn was `asyncio.create_subprocess_exec` from the daemon's Runner, not
    `subprocess.Popen` — the barrier has to cover the async door too.
    """
    import asyncio

    with pytest.raises(guard.ProductionWriteError):
        await asyncio.create_subprocess_exec("python3", "-m", "rsched.cli", "engine-run", "x")
    proc = await asyncio.create_subprocess_exec("bash", "-c", "true")
    await proc.wait()
