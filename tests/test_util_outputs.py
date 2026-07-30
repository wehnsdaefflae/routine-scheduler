"""The util-output spill store (engine/outputs.py): output too large for its observation
is saved in full to `.util_outputs/` instead of destroyed, the pointer rides the
observation that lost the middle, the store is gitignored + engine-owned + pruned, and
earlier runs' spills reach the next run through the state digest.
"""

from types import SimpleNamespace

from rsched.config import ServerConfig, load_routine
from rsched.engine import outputs
from rsched.engine.composer import state_digest
from rsched.engine.executor import dispatch
from rsched.engine.observations import OBS_CAP_CHARS, format_observation, truncate
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy

BIG = "x" * (OBS_CAP_CHARS * 2)


def _ctx(make_routine, slug="spiller", turn=7, run_ts="20260726-120000") -> RunContext:
    d = make_routine(slug=slug)
    cfg, _problems = load_routine(d)
    run_dir = d / "runs" / run_ts
    run_dir.mkdir(parents=True)
    ctx = RunContext(routine=cfg, server=ServerConfig(), registry=None, run_ts=run_ts,
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.turn = turn
    return ctx


def test_spill_keeps_only_what_the_observation_could_not_carry(make_routine):
    """The store is the recovery of a LOSS, not a mirror: an output the observation
    carried whole is already in the transcript verbatim, so nothing is written."""
    ctx = _ctx(make_routine)
    assert outputs.spill(ctx, "echoer", "small", "", out_truncated=False,
                         err_truncated=False) is None
    assert not (ctx.routine.dir / outputs.OUTPUTS_DIR).exists()


def test_spill_writes_the_full_text_and_a_relative_pointer(make_routine):
    ctx = _ctx(make_routine)
    pointer = outputs.spill(ctx, "page-fetch", BIG, "", out_truncated=True, err_truncated=False)
    assert pointer == {"stdout": f"{outputs.OUTPUTS_DIR}/20260726-120000/t7-page-fetch.out",
                       "stdout_chars": len(BIG)}
    assert "stderr" not in pointer                      # an untruncated stream is not copied
    saved = (ctx.routine.dir / pointer["stdout"]).read_text(encoding="utf-8")
    assert saved == BIG                                 # the elided middle survives in full


def test_pointer_rides_the_observation_that_lost_the_middle(make_routine):
    """No index is needed because the path appears at the moment of need."""
    ctx = _ctx(make_routine)
    pointer = outputs.spill(ctx, "page-fetch", BIG, "trace", out_truncated=True,
                            err_truncated=True)
    rendered = format_observation({"kind": "util", "name": "page-fetch", "exit": 0,
                                   "stdout": "head…tail", "stderr": "trace",
                                   "truncated": True, "full_output": pointer})
    assert "[full output]" in rendered
    assert pointer["stdout"] in rendered and pointer["stderr"] in rendered
    assert "read_file" in rendered and "instead of re-running the util" in rendered
    # …and an untruncated call says nothing at all
    assert "[full output]" not in format_observation(
        {"kind": "util", "name": "echoer", "exit": 0, "stdout": "hi", "truncated": False})


def test_first_spill_gitignores_the_store(make_routine):
    """The run-end autocommit is `git add -A` and util output can carry tokens — the
    store must never enter the routine's repo (nor ride git-sync to a remote)."""
    ctx = _ctx(make_routine)
    (ctx.routine.dir / ".gitignore").write_text("runs/\n", encoding="utf-8")
    outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    lines = (ctx.routine.dir / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "runs/" in lines                              # pre-existing entries survive
    assert f"{outputs.OUTPUTS_DIR}/" in lines
    outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    assert lines.count(f"{outputs.OUTPUTS_DIR}/") == 1   # idempotent: never appended twice


def test_a_child_run_cannot_overwrite_its_parents_spill(make_routine):
    """A subrun's turn numbering restarts, so the run key carries its sub path."""
    ctx = _ctx(make_routine, turn=3)
    parent = outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    ctx.run_dir = ctx.run_dir / "sub" / "2"
    ctx.run_dir.mkdir(parents=True)
    child = outputs.spill(ctx, "echoer", BIG + "!", "", out_truncated=True, err_truncated=False)
    assert child["stdout"] != parent["stdout"]
    assert "sub-2" in child["stdout"]
    assert (ctx.routine.dir / parent["stdout"]).read_text(encoding="utf-8") == BIG


def test_retention_prunes_to_the_newest_runs(make_routine):
    ctx = _ctx(make_routine)
    for i in range(outputs.KEEP_RUNS + 3):
        ctx.run_ts = f"20260726-1200{i:02d}"
        ctx.run_dir = ctx.routine.dir / "runs" / ctx.run_ts
        outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    kept = sorted(p.name for p in (ctx.routine.dir / outputs.OUTPUTS_DIR).iterdir())
    assert len(kept) == outputs.KEEP_RUNS
    assert kept[-1] == "20260726-120007"                 # the newest survive…
    assert "20260726-120000" not in kept                 # …the oldest are gone


def test_the_store_is_read_only_for_the_run(make_routine):
    """Engine-owned like runs/: a run reads the record of what a util returned, and does
    not rewrite it."""
    ctx = _ctx(make_routine)
    ctx.grants = GrantPolicy()
    pointer = outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    obs = dispatch({"kind": "write_file", "path": pointer["stdout"], "content": "forged"}, ctx)
    assert "engine-owned and read-only" in obs["error"]
    assert (ctx.routine.dir / pointer["stdout"]).read_text(encoding="utf-8") == BIG
    edited = dispatch({"kind": "edit_file", "path": pointer["stdout"],
                       "anchor": "xxx", "replacement": "yyy"}, ctx)
    assert "engine-owned and read-only" in edited["error"]


def test_digest_carries_earlier_runs_spills(make_routine):
    """This run's own pointers ride its observations; an output an EARLIER run paid for
    has no other route into the prompt."""
    ctx = _ctx(make_routine)
    assert outputs.digest(ctx.routine.dir) == ""         # empty store → no section at all
    assert outputs.OUTPUTS_DIR not in state_digest(ctx.routine.dir, [], [])
    outputs.spill(ctx, "page-fetch", BIG, "", out_truncated=True, err_truncated=False)
    digest = state_digest(ctx.routine.dir, [], [])
    assert f"{outputs.OUTPUTS_DIR}/20260726-120000/t7-page-fetch.out" in digest
    assert "rather than re-running the util" in digest


def test_spill_never_raises(tmp_path):
    """A failed spill must not fail the turn — the truncated observation still carries the
    head and the tail."""
    bogus = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path / "gone" / "\0bad"),
                            run_ts="t", turn=1, run_dir=tmp_path, root_run_dir=tmp_path)
    outputs.spill(bogus, "echoer", BIG, "", out_truncated=True, err_truncated=False)
    blocked = tmp_path / "blocked"
    (blocked / outputs.OUTPUTS_DIR / "t" / "t1-echoer.out").mkdir(parents=True)
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=blocked), run_ts="t", turn=1,
                          run_dir=blocked, root_run_dir=blocked)
    outputs.spill(ctx, "echoer", BIG, "", out_truncated=True, err_truncated=False)


def test_truncate_head_mode_keeps_head_and_resumes_in_sequence():
    """R45: ordered STDOUT (spilled in full) must tail-truncate — keep the head, drop the
    tail — so the reader continues IN SEQUENCE from the spill file, not lose the middle."""
    text = "A" * OBS_CAP_CHARS + "TAIL-MARKER"       # > OBS_CAP_CHARS, unique tail token
    out, trunc = truncate(text, keep="head")
    assert trunc
    head = out.split("\n[... output truncated")[0]
    assert head == text[:OBS_CAP_CHARS]              # the HEAD is kept verbatim
    assert "TAIL-MARKER" not in out                  # the TAIL is dropped, not shown
    assert f"read the spill file from char {OBS_CAP_CHARS}" in out  # resume offset named
    assert "head+tail" not in out


def test_truncate_default_keeps_both_ends():
    """Failure stderr must keep head+tail — the traceback's END is the repair material."""
    text = "HEAD" + "x" * 10000 + "TRACEBACK-END"
    out, trunc = truncate(text)
    assert trunc
    assert out.startswith("HEAD")
    assert out.endswith("TRACEBACK-END")
    assert "(head+tail)" in out

