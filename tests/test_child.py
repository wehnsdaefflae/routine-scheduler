"""The CHILD RUN contract (F338): one concept, three scheduling modes, one hand-back path.

These pin the vocabulary itself. F338 exists because three names for one thing let the prompt
copy drift until it stated something false about children, so the point of these tests is that
the mode vocabulary and the hand-back path have exactly ONE definition and every surface reads
it from there.
"""

import contextlib
import threading
import time
from types import SimpleNamespace

from helpers import run_context
from rsched.engine import child, control, subruns
from rsched.engine.observations import format_observation
from rsched.engine.run_context import RunContext


def _ctx(make_routine, slug: str) -> RunContext:
    """A real root RunContext over a scaffolded routine — the tree's lock, counter and dirs."""
    return run_context(make_routine(slug), "20260708-070000")


def test_modes_are_the_vocabulary():
    """The mode constants and MODE_NOUN's keys are the same set — a mode added to one and not
    the other is exactly the drift this module exists to prevent."""
    assert set(child.MODE_NOUN) == {child.PARALLEL, child.SEQUENTIAL, child.BRANCH}
    assert (child.PARALLEL, child.SEQUENTIAL, child.BRANCH) == (
        "parallel", "sequential", "branch")


def test_mode_noun_never_leaks_a_raw_enum():
    assert child.mode_noun(child.PARALLEL) == "parallel child run"
    assert child.mode_noun(child.SEQUENTIAL) == "sequential child run"
    assert child.mode_noun(child.BRANCH) == "branched child conversation"
    assert child.mode_noun("nonsense") == "child run"      # never the raw value


def test_handback_dirname_is_namespaced_per_child():
    """Concurrent siblings must not overwrite each other's deliverables, and the path must be
    stable enough for a parent to name it in later work."""
    assert child.handback_dirname(child.SUB, 3) == "artifacts/from-sub-3"
    assert child.handback_dirname(child.SUB, 1) != child.handback_dirname(child.SUB, 2)
    assert child.handback_dirname(child.SUB, 1).startswith(child.HANDBACK_SUBDIR + "/")


def test_every_handback_noun_is_spelled_here_and_only_here():
    """One module owns the hand-back, so it must know all three landing places — the branch
    and the background delivery used to spell their own prefix beside their own copytree,
    which is how the module that claims the contract knew only `from-sub-`.
    """
    assert child.handback_dirname(child.BACKGROUND, "bg-7") == "artifacts/from-bg-bg-7"
    assert child.handback_dirname(child.BRANCH_HANDBACK, "c-p-b1") == (
        "artifacts/from-branch-c-p-b1")


def test_collect_handback_copies_and_names_what_landed(tmp_path):
    """Paths, not a count: a parent told "3 artefact(s) were copied" has to go and list a
    directory to find out what it was given."""
    src, parent = tmp_path / "kid" / "artifacts", tmp_path / "parent"
    src.mkdir(parents=True)
    (src / "out.csv").write_text("a,b\n", encoding="utf-8")
    (src / "deep").mkdir()
    (src / "deep" / "notes.md").write_text("x", encoding="utf-8")
    (parent / "artifacts").mkdir(parents=True)
    (parent / "artifacts" / "mine.md").write_text("the parent's own", encoding="utf-8")

    paths = child.collect_handback(src, parent, child.SUB, 2)
    assert paths == ("artifacts/from-sub-2/deep/notes.md", "artifacts/from-sub-2/out.csv")
    assert (parent / "artifacts" / "from-sub-2" / "out.csv").is_file()
    assert (parent / "artifacts" / "mine.md").is_file()    # never clobbers the parent's own
    # a child that wrote nothing hands back only its summary
    assert child.collect_handback(tmp_path / "nothing", parent, child.SUB, 3) == ()


def test_one_finished_headline_for_every_mode():
    """ONE headline, the mode named INSIDE it. Before F338 each mode announced itself under a
    different noun, which is how the copy drifted apart."""
    kw = {"n": 1, "label": "draft", "workflow": "general-task", "status": "ok",
          "turns": 4, "summary": "did the thing"}
    par = control.child_finished_message(mode=child.PARALLEL, **kw)
    seq = control.child_finished_message(mode=child.SEQUENTIAL, **kw)
    for msg, mode in ((par, child.PARALLEL), (seq, child.SEQUENTIAL)):
        assert msg.startswith("CHILD RUN FINISHED (" + child.mode_noun(mode) + ")")
        assert "#1 'draft' (pattern general-task, status ok, 4 turns)" in msg
        assert "did the thing" in msg
    # only the follow-on instruction differs, because only that genuinely differs
    assert "Fold this result into your next child run's brief" in seq
    assert "Fold this result" not in par


def test_the_cap_refusal_says_the_budget_is_shared_by_the_whole_tree(make_routine, tmp_path):
    """D147-A: `max_subruns` is one cumulative total for the run TREE, and the refusal has to
    say so.

    A child that has started nothing, refused at its own first spawn, reads "child run budget
    (8) exhausted" and has no way to tell that its siblings spent the 8 — so it cannot
    distinguish a shared ceiling from a bug in its own call. The specimen (R1870/F549): three
    children numbered 5, 7 and 8, all refused at their first spawn, all finishing PARTIAL.
    """
    ctx = _ctx(make_routine, "cap-refusal")
    ctx.budgets.max_subruns = 8
    ctx.sub_counter[0] = 8

    mgr = subruns.SubrunManager(SimpleNamespace(ctx=ctx))

    reason = mgr._cap_reason(noun="child run")
    assert reason and "exhausted" in reason
    assert "shared" in reason and "tree" in reason, (
        f"the refusal says only {reason!r} — a child that started nothing cannot tell that "
        f"its siblings spent the budget, so it reads a shared ceiling as its own failure")


def test_finished_headline_names_what_the_child_handed_back():
    """The parent must never have to go looking in the child's dir — that procedure is what
    R409/R410 cost a run."""
    msg = control.child_finished_message(
        mode=child.PARALLEL, n=2, label="scan", workflow="general-task", status="ok",
        turns=3, summary="s", collected=("artifacts/from-sub-2/out.csv",))
    assert "Collected into your artifacts/: artifacts/from-sub-2/out.csv" in msg
    assert "the sender's own dir is not in your reach" in msg
    # a child that wrote nothing hands back only its summary — no dangling collection line
    assert "Collected into your artifacts/" not in control.child_finished_message(
        mode=child.PARALLEL, n=2, label="scan", workflow="general-task", status="ok",
        turns=3, summary="s")


def test_both_reporters_of_an_exit_name_the_hand_back_one_way():
    """A child's exit reaches the parent through whichever of two reporters wins a race — the
    `wait` observation or the turn-boundary announcement — so the hand-back has ONE spelling
    (`child.handback_paths_line`). The wait used to word it its own way."""
    paths = ("artifacts/from-sub-2/out.csv",)
    waited = format_observation({
        "kind": "wait", "timed_out": False, "still_running": [],
        "finished": [{"n": 2, "label": "scan", "status": "ok", "turns": 3, "mode": "parallel",
                      "summary": "s", "collected": list(paths)}]})
    announced = control.child_finished_message(
        mode=child.PARALLEL, n=2, label="scan", workflow="general-task", status="ok",
        turns=3, summary="s", collected=paths)
    line = child.handback_paths_line(paths)
    assert line in waited and line in announced


def test_a_kill_that_has_not_landed_does_not_say_terminated():
    """A child told to stop that is still inside a model call has not terminated — the parent
    hears about its exit when it lands, like any other exit."""
    still = format_observation({"kind": "kill", "n": 1, "killed": True, "status": "stopping"})
    assert "terminated" not in still and "still winding down" in still
    assert "terminated (aborted)" in format_observation(
        {"kind": "kill", "n": 1, "killed": True, "status": "aborted"})


def test_two_siblings_cannot_both_take_the_last_child(make_routine, monkeypatch):
    """`max_subruns` is ONE allowance for the whole tree, and parallel children spend it from
    their own threads. It used to be read outside the lock that guards the shared counter, so
    two siblings that both read "one left" both took it. Forced here: both siblings are held
    together between judging their call and claiming a child."""
    ctx = _ctx(make_routine, "lastchild")
    ctx.budgets.max_subruns = 2
    ctx.sub_counter[0] = 1                       # one child left in the tree's allowance
    together = threading.Barrier(2, timeout=5)

    def model_reason(_self, _action) -> None:    # a valid call, after the other one's check
        with contextlib.suppress(threading.BrokenBarrierError):
            together.wait()

    monkeypatch.setattr(subruns.SubrunManager, "_model_reason", model_reason)
    monkeypatch.setattr(subruns, "build_child", lambda _ctx, _action, **kw: SimpleNamespace(
        n=kw.get("n"), label=kw.get("label"), workflow="general-task", note=""))
    monkeypatch.setattr(subruns.SubrunManager, "_start", lambda _self, _sub: None)
    results: list[dict] = []

    def sibling() -> None:
        results.append(subruns.SubrunManager(SimpleNamespace(ctx=ctx)).spawn({"prompt": "p"}))

    threads = [threading.Thread(target=sibling) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(bool(r.get("rejected")) for r in results) == [False, True]
    assert ctx.sub_counter[0] == 2


def test_kill_all_gives_every_child_one_shared_deadline(monkeypatch):
    """The parent's exit tells every running child to stop at once and then waits KILL_JOIN_S
    for ALL of them — not KILL_JOIN_S EACH, which let children stuck in model calls (no abort
    interrupts one) hold a finish for a multiple of the grace procgroup's backstop plans for."""
    monkeypatch.setattr(subruns, "KILL_JOIN_S", 0.3)
    mgr = subruns.SubrunManager(SimpleNamespace(ctx=None))
    monkeypatch.setattr(mgr, "_collect", lambda _sub: None)
    for n in (1, 2, 3):                          # three children that never stop
        mgr.subruns[n] = SimpleNamespace(n=n, done=threading.Event(), status="running",
                                         abort_event=threading.Event(), announced=False,
                                         summary="")
    started = time.monotonic()
    killed = mgr.kill_all(reason="parent run finished (ok)")
    elapsed = time.monotonic() - started
    assert killed == 3
    assert all(s.abort_event.is_set() and s.status == "aborted" for s in mgr.subruns.values())
    assert elapsed < 0.6, f"waited {elapsed:.2f}s — one grace per stuck child"


def test_a_finished_childs_elapsed_is_how_long_it_ran():
    """The `subruns` table's elapsed column stops when the child does — it used to keep
    counting the time since the child STARTED, so a child that ran 3 s read as running for as
    long as the parent kept going."""
    mgr = subruns.SubrunManager(SimpleNamespace(ctx=None))
    done = threading.Event()
    done.set()
    mgr.subruns[1] = SimpleNamespace(n=1, label="x", workflow="general-task", mode="parallel",
                                     status="ok", summary="s", done=done,
                                     ctx=SimpleNamespace(turn=2),
                                     started_mono=100.0, ended_mono=103.0)
    assert mgr.status_table()["rows"][0]["elapsed_s"] == 3.0
