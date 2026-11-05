"""A conversation HOLDS scripts, and its rail can list them (F833).

The operator, 2026-11-02: *"why do conversations not show the routine's scripts in the right
side bar? routine runs seem to do it"* — decided 2026-11-05: conversations hold scripts. The
engine already let them: a conversation's run resolves `script` against its own dir
(`executor._script_homes` starts at `ctx.routine.dir`, which IS the conversation dir) and may
create `scripts/` like any routine (it is neither sealed nor recipe) — 33 of 139 conversations
on this instance hold 144 scripts between them. What was missing was the READ: the only scripts
route resolved slugs through the routines home, so every conversation's list 404'd and the rail
swallowed it. The conversation route is the routine route's twin, on the same reader.
"""

from __future__ import annotations

import pytest

from conftest import authed_client, make_test_server
from helpers import bare_routine, run_context, server_config
from rsched import ownseal
from rsched.engine.executor import available_scripts, script_home
from rsched.engine.fileops import _write_gate

TS = "20261105-090000"
SCRIPT = '"""profile-dir — size a directory tree.\n\nusage:\n  profile-dir <path>\n"""\n'


@pytest.fixture
def client(tmp_path):
    server = make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"))
    with authed_client(server) as c:
        yield c, tmp_path


def _conversation(tmp, slug="c-20261105-090000", *, with_script=True):
    d = bare_routine(tmp / "conversations", slug)
    if with_script:
        (d / "scripts").mkdir()
        (d / "scripts" / "profile_dir.py").write_text(SCRIPT, encoding="utf-8")
    return d


def test_the_conversation_rail_reads_the_scripts_a_reply_is_given(client):
    c, tmp = client
    _conversation(tmp)
    r = c.get("/api/conversations/c-20261105-090000/scripts")
    assert r.status_code == 200, r.text
    (row,) = r.json()["scripts"]
    assert row["name"] == "profile_dir"
    assert row["summary"] == "profile-dir — size a directory tree."
    assert "profile-dir <path>" in row["usage"]


def test_a_conversation_without_scripts_is_an_empty_list_not_an_error(client):
    c, tmp = client
    _conversation(tmp, with_script=False)
    r = c.get("/api/conversations/c-20261105-090000/scripts")
    assert r.status_code == 200 and r.json() == {"scripts": []}


def test_each_route_resolves_only_its_own_home(client):
    """A slug is unique only within a home, so neither route answers for the other's."""
    c, tmp = client
    _conversation(tmp)
    bare_routine(tmp / "routines", "worker")
    assert c.get("/api/routines/c-20261105-090000/scripts").status_code == 404
    assert c.get("/api/conversations/worker/scripts").status_code == 404
    assert c.get("/api/conversations/no-such/scripts").status_code == 404


def test_a_conversation_run_calls_and_creates_its_own_scripts(tmp_path):
    """The engine half the decision rests on: the script action resolves the conversation's
    own `scripts/`, and writing a NEW one there passes every write gate and seal."""
    d = _conversation(tmp_path)
    ctx = run_context(d, TS, server=server_config(conversations_home=d.parent,
                                                  routines_home=tmp_path / "routines"))
    assert script_home(ctx, "profile_dir").resolve() == d.resolve()
    assert available_scripts(ctx) == ["profile_dir"]
    assert _write_gate(ctx, (d / "scripts" / "tally.py").resolve()) is None
    assert "scripts" not in ownseal.sealed_names(recipe_unlocked=False)
    assert d / "scripts" in ownseal.writable_children(d, recipe_unlocked=False)
