"""The recursive task-tree read-model (web/tasktree.py): reconstruct a run's children from the
on-disk sub/ transcripts, with live stats for running nodes and nested grandchildren."""

from rsched.engine.transcript import Transcript
from rsched.readmodels.tasktree import build_tree


def test_build_tree_reconstructs_recursive_children(tmp_path):
    run = tmp_path / "runs" / "T"
    run.mkdir(parents=True)
    t = Transcript(run / "transcript.jsonl")
    t.event("subrun_start", {"n": 1, "label": "seq1", "workflow": "general-task",
                             "mode": "sequential", "depth": 1, "budget": {"turns": 8, "tokens": -1}})
    t.event("subrun_end", {"n": 1, "label": "seq1", "workflow": "general-task", "mode": "sequential",
                           "status": "ok", "turns": 5, "usage": {"in": 100, "out": 40},
                           "summary": "did it"})
    t.event("subrun_start", {"n": 2, "label": "par1", "workflow": "general-task",
                             "mode": "parallel", "depth": 1, "budget": {"turns": 4}})
    t.close()
    # a running child (#2) with its OWN running grandchild (#3)
    (run / "sub" / "2").mkdir(parents=True)
    ct = Transcript(run / "sub" / "2" / "transcript.jsonl")
    ct.event("assistant_action", {"kind": "util", "name": "x"}, turn=3, usage={"in": 20, "out": 10})
    ct.event("subrun_start", {"n": 3, "label": "grand", "workflow": "general-task",
                              "mode": "sequential", "depth": 2, "budget": {"turns": 2}})
    ct.close()

    tree = build_tree(run)
    assert [n["n"] for n in tree] == [1, 2]
    # a finished sequential subtask carries its final state + budget from the end event
    assert tree[0]["mode"] == "sequential" and tree[0]["state"] == "ok"
    assert tree[0]["turns"] == 5 and tree[0]["budget"]["turns"] == 8
    # a running parallel child gets LIVE turns summed from its own transcript
    assert tree[1]["mode"] == "parallel" and tree[1]["state"] == "running" and tree[1]["turns"] == 3
    # recursion: the running child's own child appears nested
    assert [c["n"] for c in tree[1]["children"]] == [3]
    assert tree[1]["children"][0]["label"] == "grand" and tree[1]["children"][0]["mode"] == "sequential"


def test_build_tree_empty_when_no_children(tmp_path):
    run = tmp_path / "runs" / "T"
    run.mkdir(parents=True)
    Transcript(run / "transcript.jsonl").close()
    assert build_tree(run) == []
