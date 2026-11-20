"""A CARRIED goal is dropped on the words that withdrew it, even when those words were spoken
to an EARLIER run (F911).

The grounding check reads `goals.person_texts(transcript_events(run_dir))` — this run's
transcript. An operator answer that withdrew an obligation is in the answered-questions store,
not in the transcript of the run that reads it, so a drop quoting it verbatim was refused as
"not later user words": a goal the operator withdrew could not be retired by the words that
withdrew it, and every later digest presented an obsolete obligation.

Reported by folder-reorg (R3513, folded by scheduler-builder in R3569) with
`q-20261112-170537-84-2` — *"Yes, drop b19 — it is withdrawn"*, a settled answer whose drop was
refused.

The fix is scoped deliberately: only a goal whose grounding lies in an earlier run
(`heard == -1`, what `obligations.carried` stamps) may be grounded in the store, and only on
answers this routine's own runs received. A goal set from words in THIS run keeps the index
ordering that makes `after=` mean "later than the words it came from".
"""

from __future__ import annotations

from pathlib import Path

from conftest import finish
from helpers import server_for, transcript_events
from rsched.engine import goalops, goals
from rsched.engine.runtime import run_routine

TS = "20261120-030000"


def goal(verb: str, **fields) -> dict:
    return {"say": "Keeping the goals.", "kind": "goal", "verb": verb, **fields}


def _answer(routine_dir: Path, qid: str, question: str, answer: str, *, ts: str) -> None:
    """One settled answer, in the shape `inbox_questions._archive_answer` writes."""
    from rsched.paths import atomic_write_json

    adir = routine_dir / "questions" / "answered"
    adir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(adir / f"{qid}.json",
                      {"qid": qid, "question": question, "options": [], "asked": ts,
                       "mode": "deferred", "type": "question", "answered": True,
                       "answer": answer, "answer_source": "web",
                       "answered_ts": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T03:28:43+02:00",
                       "consumed": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T03:29:00+02:00"})


def _obligation(routine_dir: Path, gid: str, text: str, quote: str) -> None:
    """One open obligation the next run carries, as `obligations.save` leaves it."""
    from rsched.paths import atomic_write_json

    atomic_write_json(routine_dir / "state" / "obligations.json",
                      {"open": [{"id": gid, "text": text, "quote": quote,
                                 "since": "2026-11-12", "from_run": "r:20261112-170537",
                                 "remains": "not started", "waits": ""}]})


def _observations(run_dir, kind: str) -> list[dict]:
    return [e["payload"] for e in transcript_events(run_dir)
            if e["type"] == "observation" and e["payload"].get("kind") == kind]


def test_a_settled_answer_from_an_earlier_run_retires_the_goal_it_withdrew(
        make_routine, scripted):
    """The defect itself: the operator's words are in the store, not in this transcript."""
    d = make_routine(slug="goaldrop", instruction="keep the folders tidy")
    (d / "state").mkdir(exist_ok=True)
    _obligation(d, "b19", "the storage attribution report is delivered",
                "attribute the storage per folder")
    _answer(d, "q-20261112-170537-84-2",
            "Should b19 stay open?", "Yes, drop b19 - it is withdrawn", ts="20261112")
    server = server_for(d)

    scripted([
        goal("drop", id="b19", quote="Yes, drop b19 - it is withdrawn"),
        finish(),
        # reached only when the drop was REFUSED: b19 is still open, so the bare finish is
        # deferred for its accounting. Keeping it lets the test assert on the refusal itself
        # instead of dying of an exhausted script.
        {**finish(), "accounting": ["b19 unmet: the drop was refused"]},
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    dropped = _observations(run_dir, "goal")[0]
    assert not dropped.get("rejected"), dropped.get("reason")
    assert dropped["ledger"][0]["status"] == "dropped"


def test_the_store_grounds_only_a_goal_whose_words_were_spoken_to_an_earlier_run(
        make_routine, scripted):
    """A goal set from THIS run's words keeps transcript ordering: `after=` still means
    "later than the words it came from", so an answer cannot retire a goal the person set
    afterwards."""
    from rsched.engine.inbox import file_message

    d = make_routine(slug="goalorder", instruction="x")
    (d / "state").mkdir(exist_ok=True)
    _answer(d, "q-20261101-000000-1", "Drop it?", "Yes, drop the mirror work", ts="20261101")
    server = server_for(d)

    def asked():
        file_message(d, "please sync the mirror", via="web")   # delivered next turn
        return {"say": "waiting for the message", "kind": "do_nothing"}

    scripted([
        asked,
        goal("add", goals=[{"text": "the mirror is synced",
                            "quote": "please sync the mirror"}]),
        goal("drop", id="b1", quote="Yes, drop the mirror work"),
        finish(),
        {**finish(), "accounting": ["b1 unmet: the drop was refused"]},
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    added, dropped = _observations(run_dir, "goal")[:2]
    assert not added.get("rejected")
    assert dropped.get("rejected"), "a this-run goal must not be retired from the store"


def test_words_in_no_answer_and_no_transcript_still_refuse(make_routine, scripted):
    """The check is not loosened into a rubber stamp: invented words are still refused, and
    the refusal says the store was read."""
    d = make_routine(slug="goalinvent", instruction="y")
    (d / "state").mkdir(exist_ok=True)
    _obligation(d, "b7", "the thing is done", "do the thing")
    _answer(d, "q-20261110-000000-2", "Keep it?", "keep going with the thing", ts="20261110")
    server = server_for(d)

    scripted([
        goal("drop", id="b7", quote="I never wrote this sentence anywhere"),
        finish(),
        {**finish(), "accounting": ["b7 unmet: the words were never written"]},
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    refused = _observations(run_dir, "goal")[0]
    assert refused.get("rejected")
    assert "answered" in refused["reason"], refused["reason"]


def test_answer_texts_are_the_persons_words_newest_last():
    """The reader itself, away from the loop: every settled answer's text, oldest first, so a
    quote spanning two answers cannot match by accident of ordering."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _answer(d, "q-20261101-000000-1", "a?", "first answer", ts="20261101")
        _answer(d, "q-20261112-000000-2", "b?", "second answer", ts="20261112")
        texts = goalops.answered_texts(d)
    assert texts == ["first answer", "second answer"]


def test_a_carried_goal_is_grounded_only_in_answers_after_it_was_asked():
    """`since` bounds the store read: an answer that predates the obligation cannot withdraw
    it, which is the store's version of `after=`."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _answer(d, "q-20261001-000000-1", "a?", "drop the old thing", ts="20261001")
        _answer(d, "q-20261115-000000-2", "b?", "drop the new thing", ts="20261115")
        carried = {**goals.blank("b3", "t", "q", source="carried", heard=-1, turn=0),
                   "since": "2026-11-12"}
        texts = goalops.answered_texts(d, since=str(carried.get("since") or ""))
    assert texts == ["drop the new thing"]
