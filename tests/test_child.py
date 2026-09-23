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
