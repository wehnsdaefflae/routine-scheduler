"""Declared informational exit codes (F711) — the `informational-exits:` header line.

The defect: `candidate_gate.py` documents exit 2 as "a poll in progress is not a usage
mistake" (F648), status polls of a detached release gate return it many times in a row, and
the repeat-failure detector counted every one of them — firing `fix-the-cause` twice in one
run on healthy behaviour. A rule that fires on healthy behaviour trains a run to dismiss it.

What is pinned here: three consecutive polls returning the DECLARED code do not trip the
detector, and a real non-zero still does.
"""

import json

import pytest
import yaml

from conftest import finish
from helpers import prompt_text, server_for
from rsched import assists as lib
from rsched.engine.exitgate import declared_informational, failure_tally_key
from rsched.engine.runtime import run_routine

TS = "20260905-190000"

POLLER = '''"""poller — poll a detached gate.
usage: poller --stamp S
informational-exits: 2
net: none
fs: roots
secrets: (none)
tags: test
"""
print("running")
'''


def _script(routine_dir, name, body):
    (routine_dir / "scripts").mkdir(exist_ok=True)
    (routine_dir / "scripts" / f"{name}.py").write_text(body, encoding="utf-8")


def _rule(server):
    """The library rule whose assist IS the repeat-failure detector."""
    home = server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    (home / "fix-the-cause.md").write_text(
        "---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: observation\n"
        "    predicate: repeated-failure\n    payload: remind\n"
        "    line: change route now\n---\n# rule: fix-the-cause — s\nbody\n",
        encoding="utf-8")


def _hold(routine_dir, slugs):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["rules"] = slugs
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def poll(name="poller"):
    return {"say": "Polling the detached gate.", "kind": "script", "name": name,
            "args": ["--stamp", "s-1"]}


def _run_polls(make_routine, scripted, monkeypatch, body, *, exit_code=2):
    """A routine holding fix-the-cause polls its own script three times, each poll
    returning `exit_code`. Returns (routine dir, endpoint, status)."""
    d = make_routine(slug="pollr")
    server = server_for(d)
    _rule(server)
    _hold(d, ["fix-the-cause"])
    _script(d, "poller", body)
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **kw: (exit_code, "running", ""))
    ep = scripted([poll(), poll(), poll(), finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    return d, ep, status


# --- the declaration ------------------------------------------------------------------------

@pytest.mark.parametrize(("line", "expected"), [
    ("informational-exits: 2", {2}),
    ("informational-exits: 2, 3", {2, 3}),
    ("informational-exits: 2 (a poll in progress), 3 — bad call shape", {2, 3}),
    ("informational-exits: 0", set()),              # 0 was never a failure
    ("informational-exits: none", set()),           # unparseable declares nothing
    ("informational-exits: 2, lots", {2}),          # the parseable half still counts
    ("exit: 0 release · 1 restore · 2 unfinished", set()),   # the PROSE line is not it
    ("net: none", set()),
])
def test_the_header_line_is_read_from_the_source(line, expected):
    src = f'"""thing — a tool.\nusage: thing\n{line}\ntags: test\n"""\nprint(1)\n'
    assert declared_informational(src) == expected


def test_a_tool_with_no_declaration_declares_nothing():
    assert declared_informational(POLLER.replace("informational-exits: 2\n", "")) == frozenset()


def test_source_that_cannot_be_parsed_is_not_a_declaration():
    assert declared_informational("") == frozenset()
    assert declared_informational("no docstring here") == frozenset()


# --- the detector, end to end ---------------------------------------------------------------

def test_three_declared_polls_do_not_trip_the_detector(make_routine, scripted, monkeypatch):
    """F711's case: `candidate_gate status` returning its documented exit 2 on poll after
    poll is the gate working as designed, not a call to fix a cause."""
    d, ep, status = _run_polls(make_routine, scripted, monkeypatch, POLLER)
    shown = prompt_text(ep)
    assert "[RULE fix-the-cause" not in shown
    assert not lib.state_path(d).exists()       # the assist never fired, so no guard was kept
    assert status == "ok"


def test_the_same_polls_undeclared_still_trip_it(make_routine, scripted, monkeypatch):
    """The control: remove ONE header line and the behaviour is exactly today's. What
    exempts the call is the declaration, never the polling shape."""
    body = POLLER.replace("informational-exits: 2\n", "")
    _d, ep, status = _run_polls(make_routine, scripted, monkeypatch, body)
    shown = prompt_text(ep)
    assert "[RULE fix-the-cause — the same call has now failed twice this run]" in shown
    assert "change route now" in shown
    assert status == "ok"


def test_an_undeclared_code_from_a_declaring_script_still_trips_it(
        make_routine, scripted, monkeypatch):
    """Only the EXACT declared codes stop counting: this script declares 2 and exits 1."""
    _d, ep, status = _run_polls(make_routine, scripted, monkeypatch, POLLER, exit_code=1)
    assert "[RULE fix-the-cause — the same call has now failed twice this run]" in prompt_text(ep)
    assert status == "ok"


# --- the seam itself ------------------------------------------------------------------------

class _Server:
    def __init__(self, home):
        self.libraries_home = home


class _Ctx:
    def __init__(self, home):
        self.server = _Server(home)


def _util_ctx(monkeypatch, tmp_path, src):
    monkeypatch.setattr("rsched.utils_lib.read_util", lambda home, name: src)
    return _Ctx(tmp_path)


def test_a_util_may_declare_informational_exits_too(monkeypatch, tmp_path):
    """BOTH callable kinds can document a non-zero exit, and both are read from source."""
    src = ('"""probe — look something up.\nusage: gu probe X\ninformational-exits: 2\n'
           'net: none\nfs: none\ntags: test\n"""\n')
    ctx = _util_ctx(monkeypatch, tmp_path, src)
    action = {"kind": "util", "name": "probe"}
    assert failure_tally_key(ctx, action, {"kind": "util", "exit": 2}) == ""
    assert failure_tally_key(ctx, action, {"kind": "util", "exit": 1}) == "util:probe"


def test_a_declared_exit_never_silences_a_real_failure(monkeypatch, tmp_path):
    """The declared code stops counting only when it is the ONLY thing wrong with the
    observation: an error, a refusal or one bad file in a batch still counts."""
    src = ('"""probe — look something up.\nusage: gu probe X\ninformational-exits: 2\n'
           'net: none\nfs: none\ntags: test\n"""\n')
    ctx = _util_ctx(monkeypatch, tmp_path, src)
    action = {"kind": "util", "name": "probe"}
    for obs in ({"kind": "util", "exit": 2, "error": "boom"},
                {"kind": "util", "exit": 2, "rejected": True},
                {"kind": "util", "exit": 2, "declined": ["TOKEN"]},
                {"kind": "util", "exit": 2, "selftest_ok": False},
                {"kind": "util", "exit": 2, "files": [{"path": "a", "error": "nope"}]}):
        assert failure_tally_key(ctx, action, obs) == "util:probe", obs


def test_a_clean_observation_is_never_tallied(monkeypatch, tmp_path):
    ctx = _util_ctx(monkeypatch, tmp_path, "")
    assert failure_tally_key(ctx, {"kind": "util", "name": "probe"},
                             {"kind": "util", "exit": 0}) == ""


def test_an_unreadable_source_leaves_the_exit_counting(monkeypatch, tmp_path):
    """Fails CLOSED: when the declaration cannot be read, the exit is a failure as before —
    and the tally yields rather than the turn."""
    def boom(home, name):
        raise OSError("gone")

    monkeypatch.setattr("rsched.utils_lib.read_util", boom)
    assert failure_tally_key(_Ctx(tmp_path), {"kind": "util", "name": "probe"},
                             {"kind": "util", "exit": 2}) == "util:probe"


def test_a_kind_that_is_not_a_callee_is_tallied_as_before(tmp_path):
    """A `write_file` has no header to read; its failure counts exactly as today."""
    obs = {"kind": "write_file", "error": "no write root"}
    key = failure_tally_key(_Ctx(tmp_path), {"kind": "write_file", "path": "a.md"}, obs)
    assert key.startswith("write_file:")


def test_the_transcript_records_the_poll_as_an_ordinary_observation(
        make_routine, scripted, monkeypatch):
    """The exemption is about the TALLY, not about hiding the exit code: the run still sees
    exit 2 in its observation and can read the gate's own verdict."""
    from rsched.engine.transcript import read_events

    d = make_routine(slug="pollr")
    server = server_for(d)
    _rule(server)
    _hold(d, ["fix-the-cause"])
    _script(d, "poller", POLLER)
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **kw: (2, "running", ""))
    scripted([poll(), poll(), finish()])
    _status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    polls = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "script"]
    assert len(polls) == 2
    assert [p["payload"]["exit"] for p in polls] == [2, 2]
    assert "[RULE" not in json.dumps(polls)
