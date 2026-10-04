"""F586 / D160-C, step B2: the INTENT CHANNEL that reaches the call B1 can already stop.

B1 gave `run_jailed` a `cancelled` callback, `CANCEL_EXIT` and the outcome mapping. Nothing
could SET it: the web had no route and the engine had no reader. B2 is those two halves, and
what these tests pin is the property the design turns on — **the cancel is keyed by TURN**.

Why that is not a detail. A bare `{"cancel": true}` flag is read by whichever call happens to be
in flight when the engine next looks. For a cancel clicked just as a long call finishes, that is
the NEXT call — one nobody asked to stop, ended with an exit code that tells its run a person
stopped it. The UI's red x renders on one action's row and therefore knows its turn, so the
intent travels as "stop the call of turn N", matches only while that turn runs, and expires by
itself when the run moves on.

The second property: `cancel_action` is the ONE control.json intent read mid-turn, because the
engine is blocked inside the call it must stop. It is still web-written and engine-read — the
engine remains the single writer of everything under `runs/`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import server_for
from rsched import utils_lib, utils_run
from rsched.engine.control import cancelled_for_turn
from rsched.engine.executor import _ended_by_cancel, do_shell, do_util

PATH_ENV = {"PATH": "/usr/bin:/bin"}


def _ctx(run_dir: Path, turn: int):
    """The minimum `cancelled_for_turn` reads: the run dir holding control.json, and the turn."""
    return SimpleNamespace(root_run_dir=run_dir, turn=turn)


def _write_control(run_dir: Path, obj: object) -> None:
    (run_dir / "control.json").write_text(json.dumps(obj), encoding="utf-8")


# -- the reader: keyed by turn, and tolerant of everything else ---------------------------

def test_the_cancel_matches_only_the_turn_it_names(tmp_path):
    """The whole safety property in one assertion pair: the operator cancelled turn 12, so the
    call of turn 13 — which may be a different util entirely — must run untouched.
    """
    _write_control(tmp_path, {"cancel_action": 12})
    assert cancelled_for_turn(_ctx(tmp_path, 12))() is True
    assert cancelled_for_turn(_ctx(tmp_path, 13))() is False
    assert cancelled_for_turn(_ctx(tmp_path, 11))() is False


def test_a_stale_cancel_expires_by_itself(tmp_path):
    """Nothing clears `cancel_action` after the call ends, and nothing needs to: a turn number
    the run has passed can never match again. That is why no engine WRITE under runs/ is
    involved — which would break the single-writer rule for a flag that self-expires.
    """
    _write_control(tmp_path, {"cancel_action": 3})
    assert cancelled_for_turn(_ctx(tmp_path, 3))() is True
    for later in (4, 5, 400):
        assert cancelled_for_turn(_ctx(tmp_path, later))() is False


@pytest.mark.parametrize("content", [
    None,                                   # no control.json at all (the common case)
    "",                                     # written empty
    "not json at all {",                    # a truncated write
    '["cancel_action", 1]',                 # valid JSON, wrong shape
    '{"cancel_action": "7"}',               # the turn as a string
    '{"cancel_action": true}',              # a bare flag — the shape this design rejects
    '{"pause": true}',                      # a control file carrying only other intents
])
def test_an_unreadable_or_wrong_shaped_control_file_never_cancels(tmp_path, content):
    """The file is ADVISORY and read every 0.25 s while a command runs. A command must never be
    killed by a parse error or by a truthy value of the wrong type, so anything that is not an
    int equal to this turn reads as "not cancelled".
    """
    if content is not None:
        (tmp_path / "control.json").write_text(content, encoding="utf-8")
    assert cancelled_for_turn(_ctx(tmp_path, 7))() is False
    assert cancelled_for_turn(_ctx(tmp_path, 1))() is False


def test_a_context_with_no_run_dir_never_cancels_instead_of_raising():
    """`do_util` builds the check UNCONDITIONALLY, so every context that reaches the executor
    must answer — including the ones no loop drives (a CLI call, a selftest, a test's fake).
    Found the hard way: the first version read `ctx.root_run_dir` directly and crashed
    `test_abort_ends_command.py::test_an_aborted_util_call_is_neither_a_failure_nor_a_tick`,
    a test this step never touched. `run_context._never_aborted` answers the same way for the
    abort, and that is the precedent followed here.
    """
    assert cancelled_for_turn(SimpleNamespace(turn=3))() is False
    assert cancelled_for_turn(SimpleNamespace())() is False


def test_the_reader_is_polled_not_snapshotted(tmp_path):
    """`cancelled_for_turn` is called ONCE per action, before the command starts, and the
    callable it returns is what `_wait` polls — so a cancel written while the call is already
    running must be seen. A reader that captured the file's state at construction would pass
    every test above and stop nothing in production.
    """
    check = cancelled_for_turn(_ctx(tmp_path, 9))
    assert check() is False
    _write_control(tmp_path, {"cancel_action": 9})
    assert check() is True


# -- the reader drives B1's seam: a real call is stopped ----------------------------------

def test_a_cancel_written_mid_call_stops_that_call(tmp_path):
    """End to end over B1's runner: the control file appears while `bash` sleeps, and the call
    comes back with the cancel's own code rather than the deadline's.
    """
    import threading

    def write_soon() -> None:
        time.sleep(1.0)
        _write_control(tmp_path, {"cancel_action": 4})

    threading.Thread(target=write_soon, daemon=True).start()
    started = time.monotonic()
    res = utils_run.run_jailed([sys.executable, "-c", "import time; time.sleep(30)"],
                               env=PATH_ENV, cwd=tmp_path, timeout=30, label="the command",
                               cancelled=cancelled_for_turn(_ctx(tmp_path, 4)))
    assert time.monotonic() - started < 15        # the cancel ended it, not the 30 s deadline
    assert res.cancelled is True
    assert res.timed_out is False
    assert res.exit_code == utils_run.CANCEL_EXIT


def test_a_cancel_naming_another_turn_leaves_the_call_alone(tmp_path):
    """The same file, the same runner, one digit different: the call runs to its own end.
    This is the test that would fail if the reader ignored the turn.
    """
    _write_control(tmp_path, {"cancel_action": 99})
    res = utils_run.run_jailed(["bash", "-c", "echo done"], env=PATH_ENV, cwd=tmp_path,
                               timeout=30, label="the command",
                               cancelled=cancelled_for_turn(_ctx(tmp_path, 4)))
    assert res.cancelled is False
    assert res.exit_code == 0
    assert "done" in res.stdout


# -- the three call sites carry it, and say so in the observation -------------------------

SLOW_UTIL = '''# /// script
# dependencies = []
# ///
"""slowpoke — sleeps (test fixture).

usage: gu slowpoke
"""
import time
time.sleep(60)
'''


def _util_ctx(home: Path, run_dir: Path, turn: int, ticks: list):
    """Like test_abort_ends_command's `_util_ctx`, plus the two fields the cancel reader needs
    (`root_run_dir`, `turn`) — and `aborted` false, so nothing here can pass by being an abort.
    """
    return SimpleNamespace(server=SimpleNamespace(libraries_home=home, sandbox="off",
                                                  routine_token=""),
                           routine=SimpleNamespace(slug="demo", dir=home, fs_read_roots=[],
                                                   fs_write_roots=[], connections={},
                                                   machines=[]),
                           grants=None, read_roots=list, write_roots=list,
                           granted_now=frozenset(), grant_args={},
                           count_util=lambda *a: ticks.append(a),
                           note_compression=lambda metrics: None,
                           aborted=lambda: False, root_run_dir=run_dir, turn=turn)


def test_a_cancelled_util_call_is_neither_a_failure_nor_a_tick(tmp_path):
    """The abort's rule, for the cancel: exit 125 is not the util failing. No `[hint]` sending a
    resumed run off to repair a util that is fine, no `[usage]`, and no reliability tick on the
    Stats tab — a call a person stopped says nothing about the util.
    """
    from rsched.engine.observations import format_observation

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_control(run_dir, {"cancel_action": 6})
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "slowpoke", SLOW_UTIL)
    ticks: list = []
    obs = do_util({"kind": "util", "name": "slowpoke", "args": []},
                  _util_ctx(tmp_path, run_dir, 6, ticks))
    assert obs["exit"] == utils_run.CANCEL_EXIT
    assert obs["cancelled"] is True
    assert "aborted" not in obs
    assert "hint" not in obs
    assert "usage" not in obs
    assert ticks == []
    text = format_observation(obs)
    assert "[hint]" not in text


def test_a_shell_command_carries_the_cancel_flag_through(tmp_path):
    """`shell` reaches `run_jailed` through `shellrun.run_shell`, which re-exports the flags
    into a dict — so the flag has one more hop to lose than the util's does, and the dict's
    shape must be the same whether a cancel happened or not.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_control(run_dir, {"cancel_action": 2})
    ctx = SimpleNamespace(server=server_for(tmp_path, sandbox="off"),
                          routine=SimpleNamespace(slug="demo", dir=tmp_path, fs_read_roots=[],
                                                  fs_write_roots=[], connections={},
                                                  machines=[]),
                          grants=None, read_roots=list, write_roots=list,
                          granted_now=frozenset(), grant_args={},
                          note_compression=lambda metrics: None,
                          aborted=lambda: False, root_run_dir=run_dir, turn=2)
    obs = do_shell({"kind": "shell", "command": "sleep 30"}, ctx)
    assert obs["exit"] == utils_run.CANCEL_EXIT
    assert obs["cancelled"] is True
    assert "timed_out" not in obs


def test_every_runner_accepts_the_cancel_parameter(tmp_path):
    """Derived from the signatures rather than restating them: all three callable kinds go
    through a runner that must take `cancelled`, and a kind whose pass-through was forgotten
    would be a cancel that silently does nothing for that kind.
    """
    import inspect

    from rsched import scripts, shellrun

    for fn in (utils_run.run_util, utils_run.run_jailed, scripts.run_script,
               shellrun.run_shell):
        assert "cancelled" in inspect.signature(fn).parameters, fn.__qualname__


def test_the_cancel_outcome_is_read_from_the_code_alone():
    """`_ended_by_cancel` needs no second look at run state, unlike `_ended_by_abort`:
    `run_jailed` reports CANCEL_EXIT only when its own callback said so.
    """
    assert _ended_by_cancel(utils_run.CANCEL_EXIT) is True
    assert _ended_by_cancel(utils_run.ABORT_EXIT) is False
    assert _ended_by_cancel(utils_run.TIMEOUT_EXIT) is False
    assert _ended_by_cancel(0) is False


# -- the web half: recording the intent ---------------------------------------------------

def _active_run(api_client, slug="testr", ts="20261004-040000"):
    """A run dir the control endpoints accept: status.json saying `running`."""
    client, tmp_path = api_client
    run_dir = tmp_path / "routines" / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")
    return client, run_dir, f"{slug}:{ts}"


def test_the_endpoint_records_the_turn_in_control_json(api_client, make_routine):
    make_routine("testr")
    client, run_dir, run_id = _active_run(api_client)
    r = client.post(f"/api/runs/{run_id}/cancel-action", json={"turn": 12})
    assert r.status_code == 200, r.text
    assert r.json()["cancel_action"] == 12
    assert json.loads((run_dir / "control.json").read_text(encoding="utf-8"))[
        "cancel_action"] == 12


def test_the_endpoint_keeps_the_other_intents(api_client, make_routine):
    """It merges (`merge_control`) rather than replacing: a pause the operator set first must
    survive a cancel, or stopping one call would silently resume the whole run.
    """
    make_routine("testr")
    client, run_dir, run_id = _active_run(api_client)
    (run_dir / "control.json").write_text(json.dumps({"pause": True}), encoding="utf-8")
    assert client.post(f"/api/runs/{run_id}/cancel-action",
                       json={"turn": 5}).status_code == 200
    control = json.loads((run_dir / "control.json").read_text(encoding="utf-8"))
    assert control["pause"] is True
    assert control["cancel_action"] == 5


def test_the_turn_is_required_and_must_be_a_real_turn(api_client, make_routine):
    make_routine("testr")
    client, run_dir, run_id = _active_run(api_client)
    assert client.post(f"/api/runs/{run_id}/cancel-action", json={}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/cancel-action",
                       json={"turn": "soon"}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/cancel-action",
                       json={"turn": 0}).status_code == 400
    assert not (run_dir / "control.json").exists()


def test_a_finished_run_has_no_call_to_cancel(api_client, make_routine):
    """409, like the pause: the control exists on the run page of a live run, and a cancel that
    arrives after the end must not leave a flag behind for a resumed leg to read.
    """
    make_routine("testr")
    client, run_dir, run_id = _active_run(api_client)
    (run_dir / "status.json").write_text(json.dumps({"state": "finished"}), encoding="utf-8")
    r = client.post(f"/api/runs/{run_id}/cancel-action", json={"turn": 4})
    assert r.status_code == 409
    assert not (run_dir / "control.json").exists()
