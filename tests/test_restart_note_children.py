"""R3644: the restart recovery note READS the dead children off disk instead of asserting them."""

from __future__ import annotations

import asyncio
import json

from conftest import mk_run
from helpers import tmp_server
from rsched.config import load_routine
from rsched.daemon import deadchildren, reap_resume
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.registry import run_index


def _child(run_dir, n, *, turns=0, says=(), finished=False):
    """A child directory as the killed leg left it: its own transcript, appended turn by turn
    by `engine/childrun.py`, with `finished=True` for one that got where it was going."""
    d = run_dir / "sub" / str(n)
    d.mkdir(parents=True)
    rows: list[dict] = [{"type": "header", "run_id": f"r:ts#sub{n}", "depth": 1}]
    for t in range(1, turns + 1):
        say = says[t - 1] if t - 1 < len(says) else f"step {t}"
        rows.append({"type": "assistant_action", "turn": t,
                     "payload": {"kind": "util", "say": say}})
    if finished:
        rows.append({"type": "finish", "payload": {"status": "ok", "summary": "done"},
                     "turns": turns})
    (d / "transcript.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


def _parent_transcript(run_dir, *starts):
    """The parent's own transcript — the only place a child's LABEL survives the leg."""
    rows = [{"type": "child_start",
             "payload": {"n": n, "label": label, "workflow": "general-task"}}
            for n, label in starts]
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_the_clause_names_the_child_its_turns_and_its_last_step(tmp_path):
    """The defect itself: an asserted "did not survive" over a transcript sitting right
    there. The leg must get the number, the label, the turn count and the step it died on."""
    _child(tmp_path, 2, turns=14,
           says=[f"step {t}" for t in range(1, 14)] + ["reading the vendor page"])
    _parent_transcript(tmp_path, (2, "research-the-vendor"))
    clause = deadchildren.note_clause(tmp_path)
    assert "sub/2" in clause
    assert "research-the-vendor" in clause
    assert "turn 14" in clause
    assert "reading the vendor page" in clause


def test_the_clause_names_the_transcript_path_the_leg_can_read(tmp_path):
    """An invitation to read is only true if the note says WHERE — the leg has no listing."""
    d = _child(tmp_path, 1, turns=3)
    clause = deadchildren.note_clause(tmp_path)
    assert str(d / "transcript.jsonl") in clause
    assert "you can read it" in clause


def test_a_child_that_finished_is_not_mourned(tmp_path):
    """A child whose transcript holds a `finish` got where it was going; listing it as a
    casualty would send the leg to re-issue work that completed."""
    _child(tmp_path, 1, turns=9, finished=True)
    assert deadchildren.note_clause(tmp_path) == ""


def test_a_child_with_no_recorded_turn_is_left_out(tmp_path):
    """A line saying `reached turn 0` reads as a finding. A directory with nothing in its
    transcript has nothing to tell the leg, so it contributes no line."""
    _child(tmp_path, 1, turns=0)
    assert deadchildren.note_clause(tmp_path) == ""


def test_every_in_flight_child_gets_its_own_line(tmp_path):
    """A note naming one of two children is half a salvage — the 2026-10-25 shape."""
    _child(tmp_path, 1, turns=120)
    _child(tmp_path, 2, turns=88)
    _child(tmp_path, 3, turns=4, finished=True)
    _parent_transcript(tmp_path, (1, "f700"), (2, "f732"), (3, "done"))
    clause = deadchildren.note_clause(tmp_path)
    assert "turn 120" in clause and "turn 88" in clause
    assert "f700" in clause and "f732" in clause
    assert "sub/3" not in clause


def test_a_run_with_no_children_gets_no_clause(tmp_path):
    """Most killed runs had none. A paragraph about children that never existed is the same
    defect pointing the other way."""
    assert deadchildren.note_clause(tmp_path) == ""


def test_an_unlabelled_child_still_gets_its_line(tmp_path):
    """The label lives in the PARENT transcript, which a kill can truncate. A missing label
    must cost the line its name, never the line."""
    _child(tmp_path, 5, turns=7)
    clause = deadchildren.note_clause(tmp_path)
    assert "sub/5" in clause and "turn 7" in clause


def test_a_half_written_transcript_line_does_not_cost_the_clause(tmp_path):
    """These transcripts end exactly where a SIGKILL landed, so the last line can be broken
    JSON. A diagnostic that dies on the damage it reports is worse than the gap."""
    d = _child(tmp_path, 1, turns=6)
    with (d / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"type": "assistant_action", "turn": 7, "payl')
    clause = deadchildren.note_clause(tmp_path)
    assert "turn 6" in clause


def test_a_very_long_say_is_capped_in_the_note(tmp_path):
    """The note is read as a prompt. One child's narration must not crowd out the rest."""
    _child(tmp_path, 1, turns=1, says=["x" * 900])
    clause = deadchildren.note_clause(tmp_path)
    assert "…" in clause
    assert len(clause) < 900


async def test_the_filed_recovery_note_carries_the_read_clause(make_routine, tmp_path):
    """End to end: what the resumed leg actually finds in its inbox. The old note asserted
    the children were gone; this one names them off the directories that are still there."""
    server = tmp_server(tmp_path, create=False)
    d = make_routine(slug="withkids")
    run_dir = mk_run(d, "20260701-070000", "running", pid=999999)
    _child(run_dir, 1, turns=14, says=["step 1"] + ["reading the vendor page"] * 13)
    _parent_transcript(run_dir, (1, "research-the-vendor"))
    cfg, _ = load_routine(d)
    runner = Runner(server, EventBus())
    run = run_index(d, "withkids")[0]

    async def _resume(c, ts, **kw):
        return f"{c.slug}:{ts}"
    runner.resume = _resume          # type: ignore[method-assign]
    assert reap_resume.resume_after_restart(runner, cfg, run, "daemon restart (deploy)")
    await asyncio.sleep(0)
    note = next(iter((d / "inbox").glob("msg-*.json"))).read_text(encoding="utf-8")
    assert "AUTOMATIC RECOVERY" in note
    assert "sub/1" in note and "research-the-vendor" in note and "turn 14" in note
    assert "re-issue what you still need" not in note
