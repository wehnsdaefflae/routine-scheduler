"""The remove_util action (util curation): the reverse-dependency helper, the un-sandboxed
removal, the util-authoring capability gate, and the handler's remove / refuse / decline paths.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched import utils_lib
from rsched.engine.actions import validate_action
from rsched.engine.interact import handle_remove_util
from rsched.grants import GrantPolicy, load_policy

UTIL = '''# /// script
# dependencies = []
# ///
"""{name} — a test util.

usage: gu {name}
tags: test
net: none
{calls}"""
X = 1
'''

AUTHORING = ("---\ntags: [t]\nrequires:\n  actions: [write_util, remove_util]\n---\n"
             "# permission: util authoring\nbody\n")


def _write(home, name, calls=""):
    body = UTIL.format(name=name, calls=(f"calls: {calls}\n" if calls else ""))
    utils_lib.write_util_file(home, name, body)


def _loop(home, *, depth=0, grants=None):
    ctx = SimpleNamespace(server=SimpleNamespace(libraries_home=home, libraries_remote=""),
                          depth=depth, grants=grants)
    return SimpleNamespace(ctx=ctx, grants=grants)


def _grants():
    # confirm "never" so the destructive-removal approval step is skipped in unit tests
    return GrantPolicy(actions=frozenset({"remove_util"}), confirm="never")


# --------------------------------------------------------------------- utils_lib helpers


def test_referenced_by_and_remove_util_file(tmp_path):
    utils_lib.ensure_library(tmp_path)
    _write(tmp_path, "base")
    _write(tmp_path, "caller", calls="base")
    assert utils_lib.referenced_by(tmp_path, "base") == ["caller"]   # reverse-dependent
    assert utils_lib.referenced_by(tmp_path, "caller") == []
    utils_lib.remove_util_file(tmp_path, "caller")
    assert not utils_lib.exists(tmp_path, "caller")
    assert utils_lib.referenced_by(tmp_path, "base") == []           # caller gone → unreferenced
    utils_lib.remove_util_file(tmp_path, "nope")                     # no-op, no raise


# ------------------------------------------------------------------------- schema validation


def test_validate_action_remove_util():
    assert validate_action({"say": "s", "kind": "remove_util", "name": "old-util"}) == []
    assert validate_action({"say": "s", "kind": "remove_util", "name": "Not A Slug"})  # not slug
    assert validate_action({"say": "s", "kind": "remove_util"})                        # no name


# ----------------------------------------------------------------------------- capability gate


def test_remove_util_capability_gate(tmp_path):
    home = tmp_path / "permissions"
    home.mkdir()
    (home / "util-authoring.md").write_text(AUTHORING, encoding="utf-8")
    granted = load_policy(home, ["util-authoring"], {"actions": ["remove_util"]})
    assert granted.allows_kind("remove_util")
    assert granted.deny({"kind": "remove_util", "name": "x"}) is None
    ungranted = load_policy(home, [], {})
    assert not ungranted.allows_kind("remove_util")
    denial = ungranted.deny({"kind": "remove_util", "name": "x"})
    assert denial and "util-authoring" in denial and "remove_util" in denial


# --------------------------------------------------------------------------------- the handler


def test_handle_remove_util_removes(tmp_path):
    utils_lib.ensure_library(tmp_path)
    _write(tmp_path, "gone")
    obs = handle_remove_util(_loop(tmp_path, grants=_grants()),
                             {"kind": "remove_util", "name": "gone"}, poll_s=0.0)
    assert obs["removed"] is True
    assert not utils_lib.exists(tmp_path, "gone")


def test_handle_remove_util_refuses_callers(tmp_path):
    utils_lib.ensure_library(tmp_path)
    _write(tmp_path, "dep")
    _write(tmp_path, "user", calls="dep")
    obs = handle_remove_util(_loop(tmp_path, grants=_grants()),
                             {"kind": "remove_util", "name": "dep"}, poll_s=0.0)
    assert obs.get("callers") == ["user"]
    assert utils_lib.exists(tmp_path, "dep")            # refused → still present


def test_handle_remove_util_missing(tmp_path):
    utils_lib.ensure_library(tmp_path)
    obs = handle_remove_util(_loop(tmp_path, grants=_grants()),
                             {"kind": "remove_util", "name": "ghost"}, poll_s=0.0)
    assert obs.get("missing") is True


def test_handle_remove_util_subrun_declines(tmp_path):
    utils_lib.ensure_library(tmp_path)
    _write(tmp_path, "sub")
    obs = handle_remove_util(_loop(tmp_path, depth=1),
                             {"kind": "remove_util", "name": "sub"}, poll_s=0.0)
    assert obs.get("declined") is True
    assert utils_lib.exists(tmp_path, "sub")            # sub-workflow can't curate
