"""A CHILD's deferred ask must land where a person reads it.

`interact.handle_ask` filed the decision record under `ctx.routine.dir/questions/pending` —
which for a child is `runs/<ts>/sub/<n>/`, a directory no surface scans. Four such records
exist on the live instance (global-utils-review, self-audit, ards, weightloss) and not one was
ever shown to anyone, while the child was told "the user will see it in the UI".
"""

import json

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.interact import handle_ask
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript

TS = "20260708-070000"


def _ctx(routine_dir, *, depth: int) -> RunContext:
    cfg, _problems = load_routine(routine_dir)
    assert cfg is not None
    run_dir = routine_dir / "runs" / TS
    if depth:
        run_dir = run_dir / "sub" / "4"
        cfg.dir = run_dir          # exactly what childrun._sub_routine does
    run_dir.mkdir(parents=True)
    return RunContext(routine=cfg, server=ServerConfig(), registry=None, run_ts=TS,
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets), depth=depth,
                      sub_n=4, sub_label="check the portal" if depth else "")


def _loop(ctx):
    class _Loop:
        dialog_qid = None
    loop = _Loop()
    loop.ctx = ctx
    return loop


def test_a_child_files_its_question_where_the_decisions_page_looks(make_routine):
    d = make_routine(slug="childask")
    ctx = _ctx(d, depth=1)
    ctx.turn = 23

    obs = handle_ask(_loop(ctx), {"kind": "ask_user", "question": "Which portal login?",
                                  "mode": "blocking"}, poll_s=0.01)

    assert obs["mode"] == "deferred"     # a child can never block the run on the user
    record = d / "questions" / "pending" / f"{obs['qid']}.json"
    assert record.is_file(), "the record must be in the ROUTINE's pending dir, not under runs/"
    assert not (ctx.run_dir / "questions").exists()
    # …and it says WHO is asking, because a child has no page of its own
    body = json.loads(record.read_text())
    assert body["question"].startswith("[child task #4 'check the portal'] ")
    assert "Which portal login?" in body["question"]


def test_a_child_and_its_parent_asking_on_the_same_turn_do_not_collide(make_routine):
    """A child shares its parent's run_ts, and the id is `q-<run_ts>-<turn>`. Allocating it
    against the child's own (empty) pending dir therefore handed both the same id, and
    `file_question` is an unconditional write.
    """
    d = make_routine(slug="samecollide")
    parent = _ctx(d, depth=0)
    parent.turn = 9
    first = handle_ask(_loop(parent), {"kind": "ask_user", "question": "parent question",
                                       "mode": "deferred"}, poll_s=0.01)

    child = _ctx(d, depth=1)
    child.turn = 9
    second = handle_ask(_loop(child), {"kind": "ask_user", "question": "child question",
                                       "mode": "deferred"}, poll_s=0.01)

    assert first["qid"] != second["qid"]
    pending = sorted(p.name for p in (d / "questions" / "pending").glob("*.json"))
    assert len(pending) == 2
