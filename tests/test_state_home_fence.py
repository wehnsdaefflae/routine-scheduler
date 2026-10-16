"""The suite must not rewrite the operator's util-stats snapshot (F684).

`readmodels/util_stats.snapshot_path()` takes no `server`: it reads `XDG_STATE_HOME` (default
~/.local/state), because a Landlock-jailed util subprocess can read that and NOT
routines_home/.control, so a snapshot kept under `.control/` would be unreachable to a
routine's own `util-stats` call. That makes it the one production write path that follows
neither the config file nor `_hermetic_home`'s patched `expand` — it resolves through
`Path.home()` — so every test that finished an engine run replaced the live
`~/.local/state/routine-scheduler/util-stats.json` with fixture data.

Both halves of the fix are pinned here because they only work together: the FENCE
(`XDG_STATE_HOME` redirected in `conftest._hermetic_home`, beside RSCHED_CONFIG and
RSCHED_DOCS_DIR) and the FLOOR (the real state dir in `production_guard._instance_paths`, so
the next un-fenced write is refused instead of silent). A fence with no floor is a fence
somebody removes.

Not R2518's `rebuilding: true` marker: the write is already atomic (`atomic_write_json`), so
a reader never sees a half-written snapshot. What was wrong was WHICH FILE got written.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import production_guard as guard
from rsched.paths import expand
from rsched.readmodels.util_stats import snapshot_path, write_util_stats_snapshot

#: The REAL snapshot, resolved the way the production code does but with the environment the
#: host actually has — read at import, before `_hermetic_home` redirects the variable.
REAL_STATE_DIR = Path(expand("~/.local/state")) / "routine-scheduler"


def test_the_state_home_is_hermetic_in_process(tmp_path):
    """The fence itself: the snapshot a test writes lands under its own tmp dir."""
    assert tmp_path in snapshot_path().parents, snapshot_path()
    assert REAL_STATE_DIR not in snapshot_path().parents


def test_the_state_home_is_hermetic_in_a_child_too(tmp_path):
    """An environment variable rather than a monkeypatch, because the engine's gate
    preparation and every util run in a SPAWNED interpreter, where no patch reaches — the
    same reason RSCHED_CONFIG is a variable.
    """
    child = subprocess.run(
        [sys.executable, "-c",
         "from rsched.readmodels.util_stats import snapshot_path; print(snapshot_path())"],
        capture_output=True, text=True, timeout=60, check=True)
    assert tmp_path in Path(child.stdout.strip()).parents


def test_the_real_state_dir_is_on_the_production_floor():
    """The floor: `_instance_paths` is computed at IMPORT, before the fixture redirects the
    variable, so what it protects is the operator's real dir even though every test runs
    under the hermetic one.
    """
    assert str(REAL_STATE_DIR) in guard._EXACT or any(
        str(REAL_STATE_DIR).startswith(p.rstrip(os.sep)) for p in guard._PREFIXES), (
        f"{REAL_STATE_DIR} is not protected — a test that resolves the real snapshot path "
        "would overwrite it silently, which is what F684 measured")


def test_a_write_to_the_real_snapshot_is_refused(monkeypatch):
    """What the floor buys: a test that reaches the real path — by resolving it without the
    fence, as `snapshot_path()` did — is stopped rather than quietly believed.
    """
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path(expand("~"))))
    target = snapshot_path()
    assert target.parent == REAL_STATE_DIR, "this test is asserting about the wrong path"
    with pytest.raises(guard.ProductionWriteError):
        target.write_text("{}", encoding="utf-8")


def test_a_finished_engine_run_leaves_the_real_snapshot_untouched(make_routine, scripted,
                                                                  tmp_path):
    """The shape that caused it, end to end: a complete engine run over a fake home. Before
    the fence this rewrote the live snapshot; now the file it writes is the tmp one, and the
    real one is not even reachable.
    """
    from conftest import finish, write_file
    from helpers import server_for
    from rsched.engine.runtime import run_routine

    before = REAL_STATE_DIR.stat().st_mtime_ns if REAL_STATE_DIR.is_dir() else None
    d = make_routine(slug="statr")
    scripted([write_file("state/a.txt"), finish()])
    server = server_for(d)
    status, _run_dir = run_routine(d, server, run_ts="20260905-090000")
    assert status == "ok"

    # the snapshot this process can write is the hermetic one…
    written = write_util_stats_snapshot(server)
    assert isinstance(written, dict)
    assert tmp_path in snapshot_path().parents
    assert snapshot_path().is_file()
    # …and the operator's own state dir was not touched by any of it
    after = REAL_STATE_DIR.stat().st_mtime_ns if REAL_STATE_DIR.is_dir() else None
    assert after == before
