"""A script call that FAILS is shown the script's declared `usage:` block (P3: told, not guessed)."""

from __future__ import annotations

from conftest import finish
from rsched import scripts
from rsched.config import ServerConfig
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-070000"

SCRIPT = '''"""ledger — append and list ledger rows.

usage: ledger add <kind> <text> | ledger list [--since DATE]
net: none
"""
'''


def _run(make_routine, scripted, monkeypatch, code: int, *, header: str = SCRIPT):
    d = make_routine(slug="scriptusage")
    (d / "scripts").mkdir()
    (d / "scripts" / "ledger.py").write_text(header, encoding="utf-8")
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda *_a, **_k: (code, "", "ledger: error: arguments missing"))
    scripted([{"say": "adding the row", "kind": "script", "name": "ledger",
               "args": ["add", "note"]}, finish()])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "test-library"
    _status, run_dir = run_routine(d, server, run_ts=TS)
    events = read_events(run_dir / "transcript.jsonl")[0]
    return next(e["payload"] for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "script")


def test_a_failed_script_call_carries_its_declared_usage(make_routine, scripted, monkeypatch):
    obs = _run(make_routine, scripted, monkeypatch, 2)
    assert "ledger add <kind> <text> | ledger list [--since DATE]" in obs["usage"]
    assert "[usage] usage: ledger add <kind> <text>" in format_observation(obs)


def test_a_successful_call_carries_no_usage(make_routine, scripted, monkeypatch):
    assert "usage" not in _run(make_routine, scripted, monkeypatch, 0)


def test_a_script_declaring_no_usage_adds_nothing(make_routine, scripted, monkeypatch):
    bare = '"""ledger — rows.\n\nnet: none\n"""\n'
    assert "usage" not in _run(make_routine, scripted, monkeypatch, 2, header=bare)


def test_usage_reads_the_header_without_running_the_script(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "boom.py").write_text(
        '"""boom — would raise.\n\nusage: boom <x>\n"""\nraise SystemExit(9)\n', encoding="utf-8")
    assert scripts.usage(tmp_path, "boom") == "usage: boom <x>"
    assert scripts.usage(tmp_path, "absent") == ""
