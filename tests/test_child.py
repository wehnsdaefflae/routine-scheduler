"""The CHILD RUN contract (F338): one concept, three scheduling modes, one hand-back path.

These pin the vocabulary itself. F338 exists because three names for one thing let the prompt
copy drift until it stated something false about children, so the point of these tests is that
the mode vocabulary and the hand-back path have exactly ONE definition and every surface reads
it from there.
"""

from rsched.engine import child, control


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
    assert child.handback_dirname(3) == "artifacts/from-sub-3"
    assert child.handback_dirname(1) != child.handback_dirname(2)
    assert child.handback_dirname(1).startswith(child.HANDBACK_SUBDIR + "/")


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


def test_finished_headline_names_what_the_child_handed_back():
    """The parent must never have to go looking in the child's dir — that procedure is what
    R409/R410 cost a run."""
    msg = control.child_finished_message(
        mode=child.PARALLEL, n=2, label="scan", workflow="general-task", status="ok",
        turns=3, summary="s", collected=("artifacts/from-sub-2/out.csv",))
    assert "Collected from the child into your artifacts/: artifacts/from-sub-2/out.csv" in msg
    assert "the child's own dir is gone from your reach" in msg
    # a child that wrote nothing hands back only its summary — no dangling collection line
    assert "Collected from the child" not in control.child_finished_message(
        mode=child.PARALLEL, n=2, label="scan", workflow="general-task", status="ok",
        turns=3, summary="s")
