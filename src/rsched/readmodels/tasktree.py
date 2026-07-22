"""The recursive task tree read-model — for the run/conversation rail's live decomposition view.

A run's children (sequential `subtask`s and parallel `spawn`s alike) live on disk under
`runs/<ts>/sub/<n>/`, each its own routine with its own transcript. This module reconstructs the
tree by READING those transcripts — it never writes anything, so the single-writer status.json
contract is untouched. The parent transcript's `subrun_start` / `subrun_end` events give each
direct child's identity, `mode`, and allotted budget; a still-running child's live turns/tokens
are summed from its own transcript, and its children come from ITS `sub/` events (recursion).
"""

from __future__ import annotations

from pathlib import Path

from ..engine.transcript import read_events

MAX_DEPTH = 8


def _live_stats(sub_dir: Path) -> tuple[int, dict]:
    """(turns, usage) summed from a running child's own transcript."""
    events, _ = read_events(sub_dir / "transcript.jsonl", 0)
    turns = 0
    usage = {"in": 0, "out": 0}
    for ev in events:
        if ev.get("type") == "assistant_action":
            turns = ev.get("turn", turns)
            u = ev.get("usage") or {}
            usage["in"] += int(u.get("in", 0) or 0)
            usage["out"] += int(u.get("out", 0) or 0)
    return turns, usage


def build_tree(run_dir: Path, depth_left: int = MAX_DEPTH) -> list[dict]:
    """The ordered list of a run's direct children, each with its live/final state and its own
    children nested (recursively). Node shape:
    {n, label, workflow, mode, budget:{turns,tokens}, state, turns, usage, summary, children:[…]}.
    `mode` is "sequential" (a subtask) or "parallel" (a spawn); `state` is running|ok|partial|
    failed|aborted.

    Memoized on the whole tree's transcript fingerprints — the rail polls this and used
    to re-read every transcript from byte 0 per tick.
    """
    from . import memo

    return memo.memoized(f"tree:{run_dir}", memo.transcript_paths(run_dir),
                         lambda: _build_tree(run_dir, depth_left))


def _build_tree(run_dir: Path, depth_left: int) -> list[dict]:
    events, _ = read_events(run_dir / "transcript.jsonl", 0)
    nodes: dict[int, dict] = {}
    order: list[int] = []
    for ev in events:
        p = ev.get("payload") or {}
        n = p.get("n")
        if ev.get("type") == "subrun_start" and isinstance(n, int):
            nodes[n] = {"n": n, "label": p.get("label"), "workflow": p.get("workflow"),
                        "mode": p.get("mode", "parallel"), "budget": p.get("budget") or {},
                        "state": "running", "turns": 0, "usage": {}, "summary": "", "children": []}
            order.append(n)
        elif ev.get("type") == "subrun_end" and n in nodes:
            nodes[n].update(state=p.get("status") or "?", turns=p.get("turns") or 0,
                            usage=p.get("usage") or {}, summary=(p.get("summary") or "")[:280])
    out: list[dict] = []
    for n in order:
        node = nodes[n]
        sub_dir = run_dir / "sub" / str(n)
        if node["state"] == "running" and sub_dir.is_dir():
            node["turns"], node["usage"] = _live_stats(sub_dir)
        if depth_left > 0 and sub_dir.is_dir():
            node["children"] = build_tree(sub_dir, depth_left - 1)
        out.append(node)
    return out
