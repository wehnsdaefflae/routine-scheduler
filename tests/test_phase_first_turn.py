"""Where each PHASE begins in the transcript — the datum the rail's nodes link to (D193).

The rail used to be a read-only diagram: it knew a phase had 7 turns but not WHICH turns, so
a reader who saw "act · 7 turns · 41k tok" had to scroll the transcript hunting for the
divider. One number closes that gap — the first turn number recorded under each phase — and
it comes out of the fold `statemap._phase_stats` already performs over `assistant_action`
events. The sub-run rows get the same treatment from `subruntree.build_tree`: the parent turn
that started the child.

This file also pins the NAMING the same change settled (D193): the concept is `phase`
everywhere the API and the UI speak of it — `/api/{routines,conversations}/{slug}/phasegraph`
returning `{"phases": [...], "current": ...}`. `stages/` stays the recipe's FILE layout and
`state` keeps its other, older meanings (run state, `state/`, read-model state); see
CLAUDE.md's "Phase, stage, state" rule.
"""

import json

import pytest

from conftest import authed_client, make_test_server
from rsched.readmodels import statemap
from rsched.readmodels.subruntree import build_tree


@pytest.fixture
def client(tmp_path, make_routine):
    """The same authed console test client test_api.py builds (its fixture is file-local)."""
    make_routine(slug="apir")   # lives under tmp_path/routines via the shared fixture
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_phase_stats_records_the_first_turn_of_each_phase(tmp_path):
    """Each phase cell carries `first_turn`: the turn number of the FIRST action recorded
    under it — where that phase begins in the transcript. The turns before any phase was
    stamped keep their own cell and their own first turn, so the unphased head of a run is
    addressable too."""
    _write(tmp_path / "transcript.jsonl", [
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header", "payload": {}},
        {"ts": "2026-07-15T10:00:10+00:00", "type": "assistant_action", "turn": 1,
         "usage": {"in": 100, "out": 10}, "payload": {}},
        {"ts": "2026-07-15T10:00:30+00:00", "type": "assistant_action", "turn": 2,
         "phase": "gather", "usage": {"in": 200, "out": 20}, "payload": {}},
        {"ts": "2026-07-15T10:01:00+00:00", "type": "assistant_action", "turn": 3,
         "phase": "gather", "usage": {"in": 50, "out": 5}, "payload": {}},
        {"ts": "2026-07-15T10:01:30+00:00", "type": "assistant_action", "turn": 4,
         "phase": "act", "usage": {"in": 50, "out": 5}, "payload": {}},
    ])
    cells = {c["phase"]: c for c in statemap.phase_stats(tmp_path)}
    assert cells[""]["first_turn"] == 1
    assert cells["gather"]["first_turn"] == 2        # not 3 — the FIRST, not the last
    assert cells["act"]["first_turn"] == 4
    assert cells["gather"]["turns"] == 2             # the instrumentation is untouched


def test_phase_stats_first_turn_is_none_when_the_transcript_numbers_nothing(tmp_path):
    """An action with no `turn` (an old transcript, a hand-written fixture) leaves
    `first_turn` None rather than inventing 0 — the UI renders a plain node for it, and a
    link to turn 0 would land nowhere."""
    _write(tmp_path / "transcript.jsonl", [
        {"ts": "2026-07-15T10:00:10+00:00", "type": "assistant_action",
         "phase": "gather", "usage": {"in": 1, "out": 1}, "payload": {}},
    ])
    assert statemap.phase_stats(tmp_path)[0]["first_turn"] is None


def test_build_tree_records_the_parent_turn_that_started_each_child(tmp_path):
    """A sub-run row links into the parent transcript at the turn that spawned it. The
    `subrun_start` event carries no turn of its own (engine/childrun.py emits it as a side
    effect of the spawning action), so the read-model takes the turn of the action it
    followed — which is exactly the turn whose box the reader wants."""
    _write(tmp_path / "transcript.jsonl", [
        {"type": "assistant_action", "turn": 1, "payload": {"kind": "util"}},
        {"type": "assistant_action", "turn": 2, "payload": {"kind": "spawn"}},
        {"type": "subrun_start", "payload": {"n": 1, "label": "first", "workflow": "general-task",
                                             "mode": "parallel", "depth": 1}},
        {"type": "assistant_action", "turn": 5, "payload": {"kind": "subtask"}},
        {"type": "subrun_start", "payload": {"n": 2, "label": "second", "workflow": "general-task",
                                             "mode": "sequential", "depth": 1}},
        {"type": "subrun_end", "payload": {"n": 2, "status": "ok", "turns": 3, "summary": "did it"}},
    ])
    tree = build_tree(tmp_path)
    assert [(n["n"], n["start_turn"]) for n in tree] == [(1, 2), (2, 5)]


def test_build_tree_start_turn_is_none_before_any_numbered_action(tmp_path):
    """A child started before any numbered action (a resumed run's re-attached children)
    reports no start turn rather than a wrong one."""
    _write(tmp_path / "transcript.jsonl", [
        {"type": "subrun_start", "payload": {"n": 1, "label": "orphan", "workflow": "general-task",
                                             "mode": "parallel", "depth": 1}},
    ])
    assert build_tree(tmp_path)[0]["start_turn"] is None


def test_phase_graph_speaks_phases(tmp_path):
    """The read-model's own vocabulary: `phase_graph` returns `phases`, not `states`. The
    concept is `phase` (the engine's persisted key); `stages/` remains the FILE layout the
    nodes are derived from."""
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "stages" / "gather.md").write_text("# Step: gather\n", encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n1. `stages/gather.md` — g.\n", encoding="utf-8")
    assert statemap.phase_graph(d) == {"phases": [{"name": "gather", "desc": "Step: gather"}],
                                       "current": ""}


def test_phasegraph_endpoint_serves_the_phase_vocabulary(client):
    """The route answers with `phases`; the old /stategraph path is gone (this repo ships no
    backwards-compatibility shims)."""
    c, tmp = client
    d = tmp / "routines" / "apir"
    (d / "stages").mkdir(exist_ok=True)
    (d / "stages" / "gather.md").write_text("# Step: gather\n", encoding="utf-8")
    d.joinpath("main.md").write_text("## Run flow\n1. `stages/gather.md` — g.\n",
                                     encoding="utf-8")
    g = c.get("/api/routines/apir/phasegraph").json()
    assert [s["name"] for s in g["phases"]] == ["gather"]
    assert c.get("/api/routines/apir/stategraph").status_code == 404
