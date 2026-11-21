"""Searching a routine's PAST RUNS (`memory_read` with `args`, engine/runsearch.py) and counting
which notes its runs READ (engine/notereads.py)."""

from __future__ import annotations

import json

from conftest import finish
from helpers import server_for
from rsched.engine import notereads, runsearch
from rsched.engine.actions import validate_action
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events


def _say(text: str) -> dict:
    return {"say": text, "kind": "read_file", "path": "main.md"}


def _search(*words: str) -> dict:
    return {"say": "Has an earlier run met this?", "kind": "memory_read", "args": list(words)}


def test_a_later_run_finds_what_an_earlier_run_saw(make_routine, scripted):
    d = make_routine(slug="searcher")
    scripted([_say("The quasar endpoint refused the token, so I used the mirror."),
              finish(summary="worked around the quasar refusal")])
    run_routine(d, server_for(d), run_ts="20261101-070000")
    scripted([_say("A quiet first look."), finish(summary="nothing about it")])
    run_routine(d, server_for(d), run_ts="20261102-070000")
    scripted([_search("quasar", "refused"), finish(summary="found it")])
    run_routine(d, server_for(d), run_ts="20261103-070000")
    events, _ = read_events(d / "runs" / "20261103-070000" / "transcript.jsonl")
    obs = next(e["payload"] for e in events if e["type"] == "observation")
    assert obs["runs"] == 2                       # the searching run is not among them
    assert obs["hits"][0]["run"] == "20261101-070000"
    assert all(h["run"] != "20261102-070000" for h in obs["hits"])
    text = format_observation(obs)
    assert text.startswith("OBSERVATION (memory_read search 'quasar refused' over 2 past runs")
    assert "«quasar»" in text
    assert (d / "runs" / runsearch.INDEX_NAME).is_file()


def test_no_match_teaches_the_query_and_any_word_is_the_fallback(tmp_path):
    routine = tmp_path / "r"
    run = routine / "runs" / "20261101-070000"
    run.mkdir(parents=True)
    (run / "transcript.jsonl").write_text(json.dumps(
        {"type": "assistant_action", "turn": 1, "payload": {
            "kind": "read_file", "say": "the gizmo answered slowly", "path": "x"}}) + "\n",
        encoding="utf-8")
    hit = runsearch.search(routine, "20261121-000000", "gizmo nonexistentword")
    assert hit["broadened"] is True and hit["hits"][0]["run"] == "20261101-070000"
    assert "match ANY of them" in runsearch.render(hit)
    miss = runsearch.search(routine, "20261121-000000", "zzz")
    assert "no past run of this routine matched" in runsearch.render(miss)
    # a transcript that changes is re-indexed, and a pruned run leaves the index
    (run / "transcript.jsonl").write_text(json.dumps(
        {"type": "finish", "payload": {"summary": "zzz turned up"}}) + "\n", encoding="utf-8")
    assert runsearch.search(routine, "x", "zzz")["hits"]
    (run / "transcript.jsonl").unlink()
    run.rmdir()
    assert runsearch.search(routine, "x", "zzz")["runs"] == 0


def test_memory_read_takes_a_name_or_words_never_both():
    base = {"say": "s", "kind": "memory_read"}
    assert any("EITHER 'name'" in p for p in validate_action(base))
    assert any("EITHER 'name'" in p for p in validate_action({**base, "name": "x",
                                                                "args": ["y"]}))
    assert not validate_action({**base, "name": "x"})
    assert not validate_action({**base, "args": ["y"]})


def test_note_reads_are_counted_per_run_and_unread_ones_named(make_routine, scripted):
    d = make_routine(slug="reader")
    (d / ".memory").mkdir()
    for name in ("used", "unused"):
        (d / ".memory" / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")
    read = {"say": "Consulting.", "kind": "memory_read", "name": "used"}
    for n in range(3):
        scripted([read, finish()])
        run_routine(d, server_for(d), run_ts=f"2026110{n + 1}-070000")
    data = json.loads((d / ".memory" / notereads.SIDECAR).read_text(encoding="utf-8"))
    assert data["runs"] == 3
    assert data["notes"]["used"] == {"first_run": 1, "last_read_run": 3, "reads": 3}
    assert data["notes"]["unused"]["reads"] == 0
    assert notereads.unread(d / ".memory", runs=2) == ["unused"]
    assert notereads.unread(d / ".memory", runs=3) == []   # first seen only 2 runs ago
