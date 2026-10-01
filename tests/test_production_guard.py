"""The suite's sandbox floor, tested like anything else (see tests/production_guard.py).

A guard nobody exercises is a guard that quietly stops guarding — which is precisely how
F394 happened one layer up, to an engine stub. So the barrier is pointed at a temporary
"instance home" and asked to do its job through the same chokepoints the codebase writes
through: `open`, `paths.atomic_write` (mkstemp + replace), `Path.mkdir`, `Path.unlink`,
`shutil.rmtree` (whose deletes are `dir_fd`-relative names).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import production_guard as guard
from rsched.paths import atomic_write, config_file, expand


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


def test_rmtree_cannot_empty_a_protected_home(tmp_path, monkeypatch):
    """`shutil.rmtree` deletes through `unlink(name, dir_fd=…)` and `rmdir(name, dir_fd=…)`.
    Read against the cwd, every bare name looked harmless, and only the final top-level
    `rmdir` — the one call made with a full path — was refused, after everything inside the
    home was already gone.
    """
    home = tmp_path / "pretend-instance"
    (home / "runs" / "r1").mkdir(parents=True)
    (home / "runs" / "r1" / "status.json").write_bytes(b"{}")
    monkeypatch.setattr(guard, "_EXACT", frozenset({str(home)}))
    monkeypatch.setattr(guard, "_PREFIXES", (f"{home}{os.sep}",))
    with pytest.raises(guard.ProductionWriteError):
        shutil.rmtree(home / "runs")
    assert (home / "runs" / "r1" / "status.json").read_bytes() == b"{}"


def test_a_dotdot_cannot_walk_a_write_into_a_protected_home(protected, tmp_path):
    """The prefix check is lexical, so the path is normalised first: `<tmp>/x/../<home>/…`
    starts with no protected prefix, and the kernel resolves it into the home all the same.
    """
    (tmp_path / "elsewhere").mkdir()
    sneaky = tmp_path / "elsewhere" / ".." / protected.name / "reports.jsonl"
    with pytest.raises(guard.ProductionWriteError):
        sneaky.write_text("{}\n", encoding="utf-8")
    assert not (protected / "reports.jsonl").exists()


def test_a_relative_symlink_into_a_protected_home_is_refused(protected, tmp_path):
    """A link into a home is a write route the string check cannot see through later, so
    making one is refused — and a RELATIVE target resolves against the link's own directory,
    not the cwd the check used to read it against.
    """
    (tmp_path / "sub").mkdir()
    with pytest.raises(guard.ProductionWriteError):
        (tmp_path / "sub" / "link").symlink_to(Path("..") / protected.name)
    assert not (tmp_path / "sub" / "link").is_symlink()


#: The config this process loads with no fixture in the way — read at IMPORT, before
#: `_hermetic_home` points RSCHED_CONFIG into each test's tmp dir.
REAL_CONFIG = config_file()


def test_the_protected_set_is_derived_and_covers_the_real_instance(monkeypatch):
    """Not an inline list: the paths come from registry.all_homes, so a fourth home added to
    the system is covered without anyone remembering to come back here. The module-level set
    is computed at IMPORT — before any fixture redirects `~` — so it names the real instance
    even though every test runs under a hermetic home. Recomputed here with the redirect
    undone, every real home is in it; a set computed under the fixture would name the
    test's tmp dir instead and fail this. (Comparing against `_instance_paths()` called under
    the fixture compared the hermetic computation with itself, which nothing could fail.)
    """
    from rsched.config import load_server_config
    from rsched.registry import all_homes

    monkeypatch.setattr("rsched.config.base.expand", expand)     # undo the hermetic `~`
    monkeypatch.setattr("rsched.config.server.expand", expand)
    real, _ = load_server_config(REAL_CONFIG)
    homes = {*all_homes(real), real.libraries_home, REAL_CONFIG.parent}
    assert all(len(Path(p).parts) > 2 for p in guard._EXACT)  # never a root-ish path
    assert {str(h) for h in homes if len(h.parts) > 2} <= guard._EXACT


def test_the_config_directory_is_hermetic_in_process_and_in_a_child(tmp_path):
    """Both secret stores, the OAuth connections and the push keys live beside config.yaml,
    so `_hermetic_home` relocates the config FILE, through the variable `config_file` reads —
    which a spawned child inherits, where no monkeypatch reaches.
    """
    from rsched.oauth.store import connections_path
    from rsched.secrets import scoped_path, secrets_path

    for path in (config_file(), secrets_path(), scoped_path("x"), connections_path()):
        assert tmp_path in path.parents, path
    child = subprocess.run(
        [sys.executable, "-c", "from rsched.paths import config_file; print(config_file())"],
        capture_output=True, text=True, timeout=60, check=True)
    assert tmp_path in Path(child.stdout.strip()).parents


def test_the_generated_docs_tree_is_hermetic_too(tmp_path):
    """Every app the suite builds mounts the generated docs at /docs, and that tree follows
    neither the config file nor the patched `expand`: without its own variable a /docs read
    served the host's ~/.cache build, so a Help assertion depended on the machine.
    """
    from rsched.docs_build import docs_out_dir

    assert tmp_path in docs_out_dir().parents, docs_out_dir()


@pytest.mark.parametrize("argv", [
    ["rsched", "engine-run", "x"],
    ["/opt/venv/bin/rsched", "daemon"],
    ["/usr/bin/python3", "-m", "rsched.cli", "engine-run", "x"],
    ["/usr/bin/python3", "-m", "rsched", "abort", "x"],
    "python -m rsched.cli engine-run x",
    # the DOCUMENTED spellings — README, CLAUDE.md, the Dockerfile's own CMD: the launcher
    # resolves the console script, so argv[0] is never `rsched` itself
    ["uv", "run", "--extra", "headroom", "rsched", "daemon"],
    "uv run rsched run-once x",
    ["uvx", "rsched", "validate"],
    ["env", "RSCHED_BIND=0.0.0.0", "rsched", "daemon"],
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
    ["uv", "run", "--script", "main.py", "rsched"],   # a util handed the word as an argument
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
