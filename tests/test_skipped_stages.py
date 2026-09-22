"""F521 — a run that never entered a stage its recipe DECLARES says so at finish.

R1681, raised by the operator: a run can silently skip a declared workflow stage and
nothing notices. The engine already observes stage ENTRY (`engine/fileops.py` stamps
`ctx.phase` when a run reads `stages/<x>.md`), but it kept only the CURRENT phase — so
the set of stages a run actually visited was never anywhere, and a run that skipped
half its recipe finished looking identical to one that worked through all of it.

The notice deliberately does NOT refuse the finish. Skipping a stage is often right (a
gather stage with nothing to gather), and a gate that blocked it would train recipes to
read modules they do not need. It NAMES the skipped stages instead, the way an `unmet`
stopping condition carries a residual: in the transcript, in status.json, and in the
finish observation the model sees.
"""
from __future__ import annotations

from conftest import finish
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from test_loop import TS, _server, probe


def _with_stages(d, *names: str):
    """Give the routine a stages/ dir AND a main.md that routes through it in order.

    Both halves matter. The modules are the declared states; main.md's run flow is what
    ORDERS them (`statemap.module_rank` ranks each module by where main.md first names
    its `<stem>.md` file). A recipe whose main.md never mentions its own stages leaves
    every module tied, and the order falls back to alphabetical — which is correct
    behaviour, and is why a test that asserts flow order has to declare a flow.
    """
    stages = d / "stages"
    stages.mkdir(exist_ok=True)
    for n in names:
        (stages / f"{n}.md").write_text(f"# Step: {n}\n\nDo the {n} work.\n", encoding="utf-8")
    flow = "\n".join(f"{i}. Read `stages/{n}.md` and follow it."
                     for i, n in enumerate(names, start=1))
    (d / "main.md").write_text(
        f"---\nmaterialized_from: {{slug: test-flow, commit: abc123, version: 1}}\n---\n\n"
        f"## Run flow\n{flow}\n\n## Completion criteria\n- Every stage accounted for.\n",
        encoding="utf-8")
    return stages


def test_a_run_that_skips_a_declared_stage_is_named_at_finish(make_routine, scripted,
                                                              monkeypatch):
    """THE DEFECT ITSELF: three stages declared, one entered, and the run finishes clean.

    Before the fix this test fails at the final assertion — the run ends with nothing
    anywhere recording that `analyse` and `record` were never entered.
    """
    from rsched.engine import verifier
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])

    d = make_routine(slug="skipper")
    _with_stages(d, "gather", "analyse", "record")

    scripted([
        {"say": "Reading my first stage.", "kind": "read_file", "path": "stages/gather.md"},
        finish(summary="gathered what there was"),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    events, _ = read_events(run_dir / "transcript.jsonl")
    skipped = [e for e in events if e["type"] == "stages_skipped"]
    assert len(skipped) == 1, "the run entered 1 of 3 declared stages and said nothing"
    payload = skipped[0]["payload"]
    assert payload["skipped"] == ["analyse", "record"]
    assert payload["entered"] == ["gather"]
    assert payload["declared"] == ["gather", "analyse", "record"]


def test_a_run_that_enters_every_declared_stage_is_silent(make_routine, scripted, monkeypatch):
    """The other half of the contract: no event when nothing was skipped.

    A notice that fires on a complete run is noise, and noise is what makes a reader stop
    reading the real ones.
    """
    from rsched.engine import verifier
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])

    d = make_routine(slug="thorough")
    _with_stages(d, "gather", "record")

    scripted([
        {"say": "Stage one.", "kind": "read_file", "path": "stages/gather.md"},
        {"say": "Stage two.", "kind": "read_file", "path": "stages/record.md"},
        finish(summary="worked through both"),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    events, _ = read_events(run_dir / "transcript.jsonl")
    assert not [e for e in events if e["type"] == "stages_skipped"]


def test_a_routine_with_no_stage_modules_is_silent(make_routine, scripted, monkeypatch):
    """A recipe that declares no stages cannot skip one — most routines are single-file."""
    from rsched.engine import verifier
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])

    d = make_routine(slug="flat")
    scripted([probe(), finish(summary="done")])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    events, _ = read_events(run_dir / "transcript.jsonl")
    assert not [e for e in events if e["type"] == "stages_skipped"]


def test_the_skipped_set_reaches_status_json(make_routine, scripted, monkeypatch):
    """The dashboard's half: 1-of-3 must be distinguishable from 3-of-3 without reading
    the transcript, which is the form the operator asked for in R1681."""
    import json

    from rsched.engine import verifier
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])

    d = make_routine(slug="dashy")
    _with_stages(d, "one", "two", "three")

    scripted([
        {"say": "Only the first.", "kind": "read_file", "path": "stages/one.md"},
        finish(summary="stopped early"),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    st = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert st["stages"]["entered"] == ["one"]
    assert st["stages"]["declared"] == ["one", "two", "three"]
    assert st["stages"]["skipped"] == ["two", "three"]


def test_entering_a_stage_twice_counts_once_and_keeps_flow_order(make_routine, scripted,
                                                                 monkeypatch):
    """Re-reading a module (a loop back to an earlier stage) is normal and must not make
    the run look like it entered more stages than the recipe declares."""
    from rsched.engine import verifier
    monkeypatch.setattr(verifier, "refuted", lambda loop, summary: [])

    d = make_routine(slug="looper")
    _with_stages(d, "alpha", "beta", "gamma")

    scripted([
        {"say": "alpha.", "kind": "read_file", "path": "stages/alpha.md"},
        {"say": "beta.", "kind": "read_file", "path": "stages/beta.md"},
        {"say": "back to alpha.", "kind": "read_file", "path": "stages/alpha.md"},
        finish(summary="looped"),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"

    events, _ = read_events(run_dir / "transcript.jsonl")
    payload = next(e for e in events if e["type"] == "stages_skipped")["payload"]
    assert payload["entered"] == ["alpha", "beta"]     # deduped, in the recipe's own order
    assert payload["skipped"] == ["gamma"]
