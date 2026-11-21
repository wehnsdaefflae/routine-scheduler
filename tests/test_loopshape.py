"""The shapes of a stuck run the identical-action streak cannot see (engine/loopshape.py): a
cycle of unchanged results, a string of DIFFERENT failing calls of one kind, and a large result
the run was already shown — plus the `shell` exit attribution (engine/exitattr.py)."""

from __future__ import annotations

from conftest import finish
from helpers import health_events, server_for
from rsched.engine import exitattr, samestub, wrongturn
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261121-080000"


def _read(path: str) -> dict:
    return {"say": f"Reading {path}.", "kind": "read_file", "path": path}


def _run(make_routine, scripted, replies, prepare=None):
    d = make_routine(slug="shaper", budgets={"max_turns": 40})
    if prepare:
        prepare(d)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events


def _observations(events: list[dict]) -> list[dict]:
    return [e["payload"] for e in events if e["type"] == "observation"]


def test_a_cycle_of_unchanged_results_is_warned_once_and_filed(make_routine, scripted):
    d, _ep, status, events = _run(make_routine, scripted, [
        _read("main.md"), _read("LEDGER.md"), _read("main.md"), _read("LEDGER.md"),
        _read("main.md"), finish()])
    assert status == "ok"
    tails = [o.get("tails", "") for o in _observations(events)]
    warned = [i for i, t in enumerate(tails) if "one cycle of 2" in t]
    assert warned == [3]          # at the 4th action, and not again while the cycle goes on
    rows = health_events(d.parent, event=wrongturn.EVENT, reason="action_cycle")
    assert len(rows) == 1


def test_a_poll_never_forms_a_cycle(make_routine, scripted):
    wait = {"say": "Waiting.", "kind": "wait", "timeout_s": 1}
    _d, _ep, _status, events = _run(make_routine, scripted, [
        _read("main.md"), wait, _read("main.md"), wait, finish()])
    assert not any("one cycle" in o.get("tails", "") for o in _observations(events))


def test_a_children_poll_never_forms_a_cycle(make_routine, scripted):
    """D241 renamed `subruns` to `children`; `_WAITING` kept the old name, so a run checking on
    its children between reads — the same answer each time, because nothing had finished yet —
    was warned it was stuck and filed as an `action_cycle`."""
    poll = {"say": "Checking my children.", "kind": "children"}
    d, _ep, _status, events = _run(make_routine, scripted, [
        _read("main.md"), poll, _read("main.md"), poll, finish()])
    assert not any("one cycle" in o.get("tails", "") for o in _observations(events))
    assert health_events(d.parent, event=wrongturn.EVENT, reason="action_cycle") == []


def test_different_calls_of_one_kind_failing_in_a_row_are_named(make_routine, scripted):
    d, _ep, _status, events = _run(make_routine, scripted, [
        _read("nope-1.txt"), _read("nope-2.txt"), _read("nope-3.txt"), _read("nope-4.txt"),
        finish()])
    tails = [o.get("tails", "") for o in _observations(events)]
    assert ["3 different read_file calls have now failed" in t for t in tails] == [
        False, False, True, False]
    assert len(health_events(d.parent, event=wrongturn.EVENT, reason="varied_failures")) == 1


def test_a_large_result_already_shown_is_not_shown_twice(make_routine, scripted):
    def prepare(d):
        (d / "big.txt").write_text("\n".join(f"line {i} of a long file" for i in range(80)),
                                   encoding="utf-8")
    _d, ep, _status, events = _run(make_routine, scripted, [
        _read("big.txt"), _read("main.md"), _read("big.txt"), finish()], prepare)
    obs = _observations(events)
    assert "same_as_turn" not in obs[0]
    assert obs[2]["same_as_turn"] == 1
    marks = ("same_as_turn", "same_chars", "tails")
    assert ({k: v for k, v in obs[2].items() if k not in marks}
            == {k: v for k, v in obs[0].items() if k not in marks})   # the record keeps it all
    rendered = format_observation(obs[2])
    assert rendered.startswith("OBSERVATION (read_file): the SAME result turn 1 returned")
    # the model was sent the stand-in, not the second copy
    last_prompt = ep.calls[-1]["messages"]
    assert sum("line 79 of a long file" in str(m.get("content")) for m in last_prompt) == 1


def test_only_a_marked_observation_is_stood_in_for():
    assert samestub.render({"kind": "read_file", "same_as_turn": None}) == ""
    assert samestub.render({"kind": "util", "same_as_turn": 4, "same_chars": 900}).startswith(
        "OBSERVATION (util): the SAME result turn 4 returned — 900 characters")


def _shell(command: str, code: int = 0) -> dict:
    return {"kind": "shell", "command": command, "exit": code}


def test_exit_attribution_names_whose_status_it_is():
    assert exitattr.note(_shell("pytest -q | tail -20")) == (
        " — the exit of `tail`, the pipe's last stage, not of `pytest`")
    assert "`||` fallback follows `make`" in exitattr.note(_shell("make test || true"))
    assert "not of `pytest`" in exitattr.note(_shell("pytest; echo done"))
    assert "not of `pytest`" in exitattr.note(_shell("cd x && pytest | tail"))
    assert "not of `wc`" in exitattr.note(_shell("ls | wc -l; echo oops >&2"))
    assert "`sleep` ran in the background" in exitattr.note(_shell("sleep 9 & wait"))


def test_exit_attribution_stays_quiet_where_the_status_is_the_checks():
    for command in ("cd x && pytest -q", "pytest", "echo 'a|b;c'", "grep -c 'x || y' f",
                    "set -o pipefail; pytest | tail", "A=1 B=2 pytest", "cmd $(a | b)",
                    "pytest 2>&1", "make &>log", "a || b && c"):
        assert exitattr.note(_shell(command)) == "", command
    assert exitattr.note(_shell("pytest | tail", 1)) == ""   # a failure is a failure already


def test_the_shell_observation_carries_it():
    text = format_observation({**_shell("pytest | tail"), "stdout": "ok", "stderr": ""})
    assert text.startswith("OBSERVATION (shell, exit 0 — the exit of `tail`")
