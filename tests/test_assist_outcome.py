"""The `outcome-questioned` assist predicate (engine/assist_outcome.py) — a person's words question
what the routine PRODUCES, so the `outcome-first` rule's line reaches the run at that moment.

Every one of the operator's outcome complaints in 2026-07..10 became more self-checking; these
are their own words, and the predicate must hear each of them — and must not hear an ordinary
instruction, which would make the line noise the run learns to skip.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.engine import assist_outcome

COMPLAINTS = ["do you even send out applications?",
              "work on your skills… you need to get better",
              "0 wins across 17 sends",
              "zero interviews so far",
              "keine Antworten bisher — bringt nichts"]
ORDINARY = ["please send the mail to Anna", "good work, thanks", "no need to ask me",
            "spend considerably more time on self-healing"]


@pytest.mark.parametrize("text", COMPLAINTS)
def test_the_operators_own_complaints_are_heard(text):
    assert assist_outcome.questions_results(text)


@pytest.mark.parametrize("text", ORDINARY)
def test_an_ordinary_instruction_is_not(text):
    assert not assist_outcome.questions_results(text)


def test_each_message_is_read_once_including_the_ones_boot_delivered(tmp_path, monkeypatch):
    texts = ["do you even send out applications?"]
    monkeypatch.setattr("rsched.engine.goals.transcript_events", lambda run_dir: [])
    monkeypatch.setattr("rsched.engine.goals.person_texts",
                        lambda events, brief="": list(texts))
    ctx = SimpleNamespace(user_replies=1, run_dir=tmp_path, brief="")
    s = SimpleNamespace(ctx=ctx, loop=SimpleNamespace())
    assert assist_outcome.outcome_questioned(s)          # boot's message, at the first look
    assert not assist_outcome.outcome_questioned(s)      # nobody spoke since: no reread
    ctx.user_replies, texts[:] = 2, [*texts, "thanks, carry on"]
    assert not assist_outcome.outcome_questioned(s)      # the new words are ordinary
