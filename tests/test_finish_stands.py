"""F658: a finish gate that RAISES must not cost the run the summary it authored.

The specimen is scheduler-builder:20261009-120002 (2026-10-09): a stale lazy import made
`finishgate.check_finish` raise AttributeError, the engine exited rc=1, and a run that had
already shipped two gated releases lost its summary, its status and its result.md — reading
`orphaned_run` on every surface since. Both halves of the fix are tested here: that the finish
path imports EAGERLY (so that instance cannot recur) and that a raising guard ends the run on
its own summary anyway (so the CLASS cannot).
"""

import ast
from pathlib import Path

import pytest

from helpers import health_events, server_for
from rsched.engine import finishgate, finishstands
from rsched.engine.control import RunAborted
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import read_json

SRC = Path(__file__).resolve().parents[1] / "src" / "rsched" / "engine"
TS = "20260101-000000"
STALE_IMPORT = "module 'rsched.engine.verifier' has no attribute 'unentered'"


def probe(say="Doing work."):
    return {"say": say, "kind": "util", "name": "nope", "args": []}


def exploding_gate(loop, action, ctx):
    """The 2026-10-09 failure, verbatim."""
    raise AttributeError(STALE_IMPORT)


# ---- option 2: the finish path imports at module scope ------------------------------------

def _lazy_imports(module: str) -> list[str]:
    """Every `from . import X` / `from .. import X` INSIDE a function body of that module."""
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    found: list[str] = []
    for func in [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(func):
            if isinstance(node, ast.ImportFrom) and node.level:
                found.extend(f"{func.name} -> {a.name}" for a in node.names)
    return found


def test_the_finish_gate_imports_the_whole_finish_path_at_module_scope():
    """The gate's three lazy imports (`verifier`, `assist`, `goalreached`) are what made the
    2026-10-09 loss possible: the engine holds this module's bytecode from boot, but a lazy
    import resolves its target FRESH FROM DISK at the finish, so a run that edits src/rsched/
    reaches its ending with an old caller calling a new module. None of the three imports
    anything that imports the gate back, so there was never a cycle to avoid.
    """
    assert _lazy_imports("finishgate.py") == []


def test_goal_check_imports_its_verifier_at_module_scope():
    """`goal check` runs the same claim verification a finish does — same shape, same loss."""
    assert _lazy_imports("goalops.py") == []


def test_the_guard_keeps_its_one_lazy_import_and_says_why():
    """finishstands -> finishgate STAYS lazy (at module scope it cycles through assist). It is
    safe where the gate's own were not — a pass-through naming no symbol of the gate's — and
    the module must keep saying so, or a later reader will "fix" it the wrong way.
    """
    assert _lazy_imports("finishstands.py") == ["check_finish_or_stand -> finishgate"]
    assert "cycle" in (SRC / "finishstands.py").read_text(encoding="utf-8")


# ---- option 3: the guard, through real runs ----------------------------------------------

def test_a_raising_finish_gate_still_records_the_authored_summary(make_routine, scripted,
                                                                  monkeypatch):
    """The heart of F658: the run ends `ok` on ITS OWN summary and writes result.md."""
    monkeypatch.setattr(finishgate, "check_finish", exploding_gate)
    d = make_routine(slug="gateboom")
    scripted([probe(), {"say": "done", "kind": "finish", "status": "ok",
                        "summary": "shipped 0.402.0 and 0.403.0, both gated green"}])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok"                       # not a crash, not partial
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert result.startswith("shipped 0.402.0 and 0.403.0, both gated green")
    events, _ = read_events(run_dir / "transcript.jsonl")
    fin = next(e for e in events if e["type"] == "finish")
    assert fin["payload"]["authored"] is True   # the model's words, not an engine string
    assert read_json(run_dir / "status.json")["outcome"] == "ok"


def test_the_failure_is_its_own_health_event_naming_the_cause(make_routine, scripted,
                                                             monkeypatch):
    """Distinguishable from a gate that ran and rejected, and from the engine crash it used to
    be — with enough of the traceback to recognise it without opening the transcript."""
    monkeypatch.setattr(finishgate, "check_finish", exploding_gate)
    d = make_routine(slug="gateboom2")
    scripted([probe(), {"say": "done", "kind": "finish", "status": "partial",
                        "summary": "one release shipped"}])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "partial"                  # the run's own status, carried through
    rows = health_events(d.parent, event=finishstands.EVENT, routine="gateboom2")
    assert len(rows) == 1
    assert "unentered" in rows[0]["detail"] and "STOOD" in rows[0]["detail"]
    assert rows[0]["status"] == "partial"

    events, _ = read_events(run_dir / "transcript.jsonl")
    errors = [e for e in events if e["type"] == "error"
              and e["payload"].get("where") == "finishgate"]
    assert len(errors) == 1 and errors[0]["payload"]["stood"] is True


def test_a_gate_that_defers_is_untouched(make_routine, scripted, monkeypatch):
    """The guard must not turn a REJECTION into a pass: a gate returning None still sends the
    run round again, and files no health event. A guard that swallowed verdicts would be worse
    than the crash it replaces."""
    real = finishgate.check_finish
    calls = {"n": 0}

    def deferring_once(loop, action, ctx):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(loop, action, ctx)

    monkeypatch.setattr(finishgate, "check_finish", deferring_once)
    d = make_routine(slug="gatedefer")
    scripted([probe(),
              {"say": "done", "kind": "finish", "status": "ok", "summary": "first try"},
              {"say": "again", "kind": "finish", "status": "ok", "summary": "second try"}])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok" and calls["n"] == 2
    assert (run_dir / "result.md").read_text(encoding="utf-8").startswith("second try")
    assert health_events(d.parent, event=finishstands.EVENT) == []


def test_an_abort_is_not_a_guard_failure(monkeypatch):
    """RunAborted and the interpreter's own exits pass straight through: those are the engine
    being stopped on purpose, and swallowing one would make a run unkillable at its finish."""
    def aborting(loop, action, ctx):
        raise RunAborted("stop")

    monkeypatch.setattr(finishgate, "check_finish", aborting)
    with pytest.raises(RunAborted):
        finishstands.check_finish_or_stand(None, {"status": "ok", "summary": "x"}, None)


def test_the_event_is_not_cooled_down_as_a_standing_condition():
    """F653 governs STANDING CONDITIONS only. Two broken finishes are two facts — the second
    must never be dropped as a repeat of the first."""
    from rsched.health_cooldown import REPEAT_COOLDOWN_HOURS

    assert finishstands.EVENT not in REPEAT_COOLDOWN_HOURS


def test_the_health_detail_names_the_error_and_the_frame():
    formatted = ("Traceback (most recent call last):\n"
                 '  File "/x/finishgate.py", line 239, in check_finish\n'
                 "    early = verifier.unentered(claims)\n"
                 f"AttributeError: {STALE_IMPORT}\n")
    line = finishstands._one_line(formatted)
    assert "unentered" in line and "finishgate.py" in line and len(line) <= 500
    assert finishstands._one_line("") == "the finish gate raised"
