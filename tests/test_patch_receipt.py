"""R2778 — the asking RUN is told whether its `config_patch` was applied.

The measured harm: a routine asked for a budget raise with a `config_patch`, the operator
answered with prose that read as approval, the run recorded the raise as applied and then
planned a 600-turn budget against a still-live 400-turn ceiling — `budget_exhausted` twice
(2026-10-19T08:03:39 and 2026-10-20T07:27:18, both `limit=400`).

R2189 built the half that tells the person who CLICKED (`config_patch_applied` in the answer
file and the HTTP response). Nothing carried that fact to the run: `collect_deferred_answers`
dropped it and the composer rendered `- Q: … / A: …` and nothing else.

The trap these tests exist to pin: the `approve & apply` button PATCHes the target and THEN
posts an ordinary answer, so an answer file written for a SUCCESSFULLY APPLIED patch also said
`config_patch_applied: False`. Telling a run "NOT APPLIED" off that flag alone would be a
falsehood on the one path that works — strictly worse than the old silence. So both directions
are tested here, and neither is allowed to regress into the other.

Lives in its own file: `tests/test_composer.py` is at its size ceiling (size_ratchet.json),
and the rule there is "put the addition in its own file".
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import authed_client, make_test_server
from helpers import run_context
from rsched.engine.composer import state_digest
from rsched.engine.inbox_questions import collect_deferred_answers
from rsched.paths import atomic_write_json

QID = "q-20261021-013505-40"
QUESTION = "Raise my turn budget to 600?"


@pytest.fixture
def client(tmp_path, make_routine):
    """The same authed API client `tests/test_api.py` builds — the routine `apir` under
    tmp_path/routines, served by a real app."""
    make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _seed_pair(routine_dir: Path, answer_extra: dict) -> list[dict]:
    """One answered deferred question on disk, collected exactly as a run's boot collects it."""
    atomic_write_json(routine_dir / "questions" / "pending" / f"{QID}.json",
                      {"qid": QID, "question": QUESTION, "options": [], "mode": "deferred",
                       "asked": "20261021-013505",
                       "config_patch": {"budgets": {"max_turns": 600}}})
    atomic_write_json(routine_dir / "inbox" / f"answer-{QID}.json",
                      {"qid": QID, "text": "Ok", "source": "web", "ts": "2026-10-21T04:05:17",
                       **answer_extra})
    consumed = routine_dir / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    return collect_deferred_answers(routine_dir, consumed)


def test_an_unapplied_patch_reaches_the_run_as_not_applied(make_routine):
    """The reported defect. An affirmative-reading answer ("Ok") on a patch the operator never
    clicked must reach the run as NOT APPLIED, in words it cannot plan around — the run that
    read only "Ok" planned 600 turns against a live ceiling of 400.
    """
    d = make_routine(slug="uir")
    pairs = _seed_pair(d, {"config_patch_applied": False, "config_patch_target": "uir"})
    assert len(pairs) == 1, pairs
    assert pairs[0]["config_patch_applied"] is False, (
        f"the answer pair must carry the patch fact, as `request`/`decision` already do: {pairs}")

    digest = state_digest(d, pairs, [])
    assert "config_patch NOT APPLIED" in digest, (
        f"the run reads the digest, not the answer file — it must SAY so: {digest}")
    assert "read the live value before planning on it" in digest, digest
    # and the answer itself is still delivered
    assert "A: Ok" in digest, digest


def test_an_applied_patch_reaches_the_run_as_applied(make_routine):
    """The other direction, and the trap: the `approve & apply` button applies the patch and
    then posts an ordinary answer. A run told "NOT APPLIED" there would be told a falsehood on
    the one path that works."""
    d = make_routine(slug="uir")
    pairs = _seed_pair(d, {"config_patch_applied": True, "config_patch_target": "uir"})
    assert pairs[0]["config_patch_applied"] is True, pairs

    digest = state_digest(d, pairs, [])
    assert "config_patch APPLIED" in digest, digest
    assert "is live on uir" in digest, digest
    assert "NOT APPLIED" not in digest, (
        f"an APPLIED patch must never render the unapplied warning: {digest}")

    # The DURABLE record keeps it too (F525's archive is what the Decisions page reads once a
    # run has consumed the answer). Without this the verdict survives only until the run that
    # reads it: the settled card would offer `approve & apply` again for a patch already live.
    archived = json.loads((d / "questions" / "answered" / f"{QID}.json")
                          .read_text(encoding="utf-8"))
    assert archived["config_patch_applied"] is True, archived


def test_an_answer_with_no_patch_renders_exactly_as_before(make_routine):
    """The control. A question with no `config_patch` gains no line, so no run learns to read a
    patch verdict into an ordinary answer."""
    d = make_routine(slug="uir")
    atomic_write_json(d / "questions" / "pending" / "q-plain.json",
                      {"qid": "q-plain", "question": "Which colour?", "options": [],
                       "mode": "deferred", "asked": "20261021-013505"})
    atomic_write_json(d / "inbox" / "answer-q-plain.json",
                      {"qid": "q-plain", "text": "green", "source": "web"})
    consumed = d / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    pairs = collect_deferred_answers(d, consumed)

    assert "config_patch_applied" not in pairs[0], pairs
    digest = state_digest(d, pairs, [])
    assert "config_patch" not in digest, digest
    assert "- Q: Which colour?\n  A: green" in digest, digest


def test_a_patch_answered_mid_run_tells_the_live_run_too(make_routine):
    """The SECOND door to the same harm. An answer to a question THIS run filed is delivered at
    the live turn boundary (F195, `control.drain_injections`), not only in a boot digest — and
    that path rendered the words alone. A run that asked, stayed alive and was answered "Ok"
    would still have had to guess whether the ceiling moved.
    """
    from rsched.engine import control
    from rsched.engine.transcript import Transcript

    d = make_routine(slug="uir")
    ctx = run_context(d, "20261021-013505")
    ctx.depth = 0
    ctx.transcript = Transcript(d / "transcript.jsonl")
    atomic_write_json(d / "questions" / "pending" / f"{QID}.json",
                      {"qid": QID, "question": QUESTION, "options": [], "mode": "deferred",
                       "asked": "20261021-013505",
                       "config_patch": {"budgets": {"max_turns": 600}}})
    atomic_write_json(d / "inbox" / f"answer-{QID}.json",
                      {"qid": QID, "text": "Ok", "source": "web",
                       "config_patch_applied": False, "config_patch_target": "uir"})
    loop = SimpleNamespace(ctx=ctx, messages=[], consumed_dir=d / "consumed",
                           resume=False, executed_actions=1,
                           subruns=SimpleNamespace(subruns={}))

    control.drain_injections(loop)

    assert len(loop.messages) == 1, loop.messages
    text = loop.messages[0]["content"]
    assert "config_patch NOT APPLIED" in text, (
        f"the live run gets the same verdict the digest states, or the mid-run door keeps the "
        f"defect: {text}")
    assert "Ok" in text, text


def test_the_answer_route_records_a_patch_the_button_applied(client):
    """The seam that makes the flag true in BOTH directions: the answer POST carries an explicit
    applied signal, which the button sets after its PATCH verified every key landed.

    Discriminating any other way was rejected: the button's canned text ("approved & applied the
    proposed config change") is a weak proxy AND `config-optimizer` already reads it, so making
    it load-bearing inside the engine would freeze a UI string into a contract.
    """
    c, tmp = client
    pending = tmp / "routines" / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-applied.json",
                      {"qid": "q-applied", "question": "Raise the turn budget?", "options": [],
                       "asked": "20261021", "mode": "deferred",
                       "config_patch": {"budgets": {"max_turns": 120}}})

    r = c.post("/api/questions/q-applied/answer",
               json={"text": "approved & applied the proposed config change",
                     "config_patch_applied": True})
    assert r.status_code == 200, r.text
    assert r.json().get("config_patch_applied") is True, r.json()

    answer = json.loads((tmp / "routines" / "apir" / "inbox" / "answer-q-applied.json")
                        .read_text(encoding="utf-8"))
    assert answer["config_patch_applied"] is True, (
        f"the answer file is what the RUN reads — it must not call an applied patch "
        f"unapplied: {answer}")
    assert "NOT applied" not in answer.get("config_patch_note", ""), answer


def test_the_questions_api_says_whether_a_patch_was_applied(client):
    """`/api/questions` carried no applied field at all on a patch-carrying row, so no reader
    could tell an applied patch from an answered-but-unapplied one without reading the target's
    config file. `config-optimizer` reads this API rather than the prompt.
    """
    c, tmp = client
    pending = tmp / "routines" / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-row.json",
                      {"qid": "q-row", "question": "Raise it?", "options": [],
                       "asked": "20261021", "mode": "deferred",
                       "config_patch": {"budgets": {"max_turns": 120}}})
    c.post("/api/questions/q-row/answer", json={"text": "no, leave it"})

    row = next(q for q in c.get("/api/questions").json() if q["qid"] == "q-row")
    assert row.get("answered") is True, row
    assert row.get("config_patch_applied") is False, (
        f"an answered patch row must state the patch verdict, not leave the reader to guess "
        f"it from the target's config: {row}")
