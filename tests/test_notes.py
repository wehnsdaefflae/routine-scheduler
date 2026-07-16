"""The note channel (engine/notes.py): the per-action `note` field is engine-filed to
state/notes.md — stamped, truncated, best-effort, at no turn cost — and its tail reaches
the next run through the state digest.
"""

from types import SimpleNamespace

from rsched.config import ServerConfig, load_routine
from rsched.engine import notes
from rsched.engine.composer import state_digest
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript


def _ctx(make_routine, slug="noter", turn=3, phase="gather") -> RunContext:
    d = make_routine(slug=slug)
    cfg, _problems = load_routine(d)
    run_dir = d / "runs" / "20260716-180000"
    run_dir.mkdir(parents=True)
    ctx = RunContext(routine=cfg, server=ServerConfig(), registry=None,
                     run_ts="20260716-180000", run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.turn = turn
    ctx.phase = phase
    return ctx


def test_capture_appends_stamped_self_addressed_lines(make_routine):
    ctx = _ctx(make_routine)
    notes.capture(ctx, {"kind": "util", "name": "websearch", "note":
                        "portal 2's search silently caps at 20 results — paginate."})
    ctx.turn = 4
    notes.capture(ctx, {"kind": "read_file", "path": "state/x.md", "note": "second finding"})
    text = (ctx.routine.dir / "state" / "notes.md").read_text(encoding="utf-8")
    assert text.startswith("# Notes")                 # header written once
    assert text.count("# Notes") == 1
    lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    # the stamp is an ADDRESS into the transcript: run · turn · phase · action
    assert "[20260716-180000 · turn 3 · gather · util websearch]" in lines[0]
    assert "paginate" in lines[0]
    assert "read_file state/x.md" in lines[1]


def test_capture_skips_empty_and_truncates_runaways(make_routine):
    ctx = _ctx(make_routine, slug="noter2")
    notes.capture(ctx, {"kind": "wait"})              # no note → no file
    assert not (ctx.routine.dir / "state" / "notes.md").exists()
    notes.capture(ctx, {"kind": "wait", "note": "x" * 900})
    text = (ctx.routine.dir / "state" / "notes.md").read_text(encoding="utf-8")
    assert "…[truncated]" in text
    assert len(text) < 700                            # 500 chars + stamp + header line


def test_digest_carries_the_notes_tail(make_routine):
    ctx = _ctx(make_routine, slug="noter3")
    for n in range(12):
        ctx.turn = n + 1
        notes.capture(ctx, {"kind": "wait", "note": f"finding number {n}"})
    digest = state_digest(ctx.routine.dir, [], [])
    assert "Recent notes" in digest
    assert "finding number 11" in digest              # the tail…
    assert "finding number 0" not in digest           # …not the whole file (last 10)
    # absent file → no section
    other = _ctx(make_routine, slug="noter4")
    assert "Recent notes" not in state_digest(other.routine.dir, [], [])


def test_capture_never_raises(tmp_path):
    """A failed capture must not fail the turn — the note is on the transcript anyway.
    Two failure flavors: a degenerate path (ValueError before the OS is reached) and a
    real filesystem refusal (notes.md exists as a DIRECTORY → IsADirectoryError)."""
    bogus = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path / "gone" / "\0bad"),
                            run_ts="t", turn=1, phase="")
    notes.capture(bogus, {"kind": "wait", "note": "x"})   # must not raise
    blocked = tmp_path / "blocked"
    (blocked / "state" / "notes.md").mkdir(parents=True)  # the file slot is a directory
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=blocked), run_ts="t", turn=1, phase="")
    notes.capture(ctx, {"kind": "wait", "note": "x"})     # must not raise either


def test_loop_files_notes_during_a_run(make_routine, scripted):
    """End-to-end through the engine loop: an action carrying a note lands in
    state/notes.md before the run finishes, stamped with the live turn."""
    from conftest import finish, write_file
    from rsched.engine.runtime import run_routine

    d = make_routine(slug="noter5")
    server = ServerConfig()
    server.routines_home = d.parent                       # hermetic, like test_loop._server
    server.libraries_home = d.parent.parent / "test-library"
    scripted([
        {**write_file("state/probe.txt", content="probe", say="Probing."),
         "note": "the probe write is the canary — keep it"},
        finish(),
    ])
    status, _run_dir = run_routine(d, server, run_ts="20260716-190000")
    assert status == "ok"   # run_routine returns the FINISH status, not the run state
    text = (d / "state" / "notes.md").read_text(encoding="utf-8")
    assert "turn 1" in text
    assert "the probe write is the canary — keep it" in text
