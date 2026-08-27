"""MIGRATION(expires=2026-09-30) guard: force named seed utils over their live copies.

`sync_seed_utils` never overwrites — by design, so it cannot clobber the user's own revisions
— which means a util FIXED in util-seed/ cannot reach an existing library on its own. This
migration is the deliberate exception, and it is a NAMED list because the drift runs both
ways: five other utils are newer in production, and a blanket seed-wins would reset
`net: outbound` to `net: none` on two of them (undeclared network = no TCP in the sandbox).
"""

import pytest

from rsched import utils_lib, utils_run
from rsched.config import ServerConfig
from rsched.migrate_seed_utils import FORCE_FROM_SEED, migrate_seed_utils


@pytest.fixture
def lib(tmp_path):
    home = tmp_path / "lib"
    utils_lib.ensure_library(home)
    return home


def _server(lib):
    return ServerConfig(libraries_home=lib, sandbox="off")


def test_named_seed_utils_replace_a_stale_live_copy(lib):
    for name in FORCE_FROM_SEED:
        utils_lib.write_util_file(lib, name, "# stale live copy\n")
    n = migrate_seed_utils(_server(lib))
    assert n == len(FORCE_FROM_SEED)
    for name in FORCE_FROM_SEED:
        live = utils_lib.read_util(lib, name)
        assert live and "stale live copy" not in live
        assert f"{name} —" in live          # the real seed docstring landed
    assert migrate_seed_utils(_server(lib)) == 0        # idempotent: identical content


def test_only_the_named_utils_are_touched(lib):
    utils_lib.write_util_file(lib, "dir-tree", "# a live revision that must survive\n")
    migrate_seed_utils(_server(lib))
    assert "must survive" in (utils_lib.read_util(lib, "dir-tree") or "")


def test_a_seed_copy_failing_its_selftest_is_rolled_back(lib, monkeypatch):
    """Same gate write_util applies. A seed copy that cannot pass its own selftest on THIS
    machine is not an improvement, and reverting beats shipping it.
    """
    for name in FORCE_FROM_SEED:
        utils_lib.write_util_file(lib, name, "# previous live copy\n")
    monkeypatch.setattr(utils_run, "selftest", lambda *a, **k: (False, "boom"))
    assert migrate_seed_utils(_server(lib)) == 0
    for name in FORCE_FROM_SEED:
        assert utils_lib.read_util(lib, name) == "# previous live copy\n"


def test_git_sync_seed_carries_the_conflict_holding_contract(lib):
    """The reason git-sync is on the list at all — the library-sync routine's recipe tells it
    to hold a conflicted rebase open, which the pre-0.166 live copy cannot do."""
    migrate_seed_utils(_server(lib))
    src = utils_lib.read_util(lib, "git-sync") or ""
    assert "--on-conflict" in src and "hold" in src
    assert "modify-delete" in src and "add-add" in src      # the classes it refuses to guess
    assert "rescue" in src.lower()                          # the remote tip is pinned first
