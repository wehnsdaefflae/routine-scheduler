"""D175: the conversation finish flow OFFERS a playbook when the conversation did real work.

The operator answered "do we still need playbooks?" with option (c), 2026-10-10 — keep the
feature and make it earn its place, explicitly as a BET that nothing was ever distilled because
nothing ever suggested it. So what is pinned here is the predicate the offer rests on: it fires
on a finished conversation that spent real turns, and on nothing else. If the offer ships and
the library still holds one playbook months later, the bet is refuted by evidence rather than
by inference — which is the whole reason the row was kept open.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.web.playbook_offer import SUBSTANTIAL_TURNS, offer


def _info(*runs):
    """A RoutineInfo-shaped stand-in: `runs` newest-first, as the registry yields them."""
    return SimpleNamespace(runs=list(runs))


def _run(turn: int, summary: str = "did the thing") -> SimpleNamespace:
    return SimpleNamespace(turn=turn, summary=summary)


def test_a_conversation_that_did_real_work_is_offered_a_playbook():
    o = offer(_info(_run(SUBSTANTIAL_TURNS)), bound_playbook=None)
    assert o["show"] is True
    assert o["mode"] == "save"
    assert str(SUBSTANTIAL_TURNS) in o["reason"]
    assert "playbook" in o["reason"]
    # 0.443.1: the console leads with "This conversation did real work — ", so a reason
    # beginning "N turns of work" rendered "did real work — 23 turns of work —". The two
    # halves live in different files and each read fine alone; only the render showed it.
    assert "turns of work" not in o["reason"]


def test_a_short_exchange_is_not_nagged():
    """The feature earns its place by asking at the right moment, so a one-reply question —
    the commonest conversation shape — must not be asked at all."""
    o = offer(_info(_run(2)), bound_playbook=None)
    assert o["show"] is False
    assert "2 turns" in o["reason"]
    assert str(SUBSTANTIAL_TURNS) in o["reason"], "the reason must say what the bar is"


def test_a_live_conversation_waits_for_its_reply():
    """A run with no summary has not finished its reply; offering mid-work would distil half
    a procedure. `latest_with_summary` falls back to the newest run of any state, so the
    summary-less case is reachable and must be handled here rather than relied upon there."""
    o = offer(_info(_run(40, summary="")), bound_playbook=None)
    assert o["show"] is False
    assert "still working" in o["reason"]


def test_a_conversation_that_never_ran_is_not_offered_anything():
    o = offer(_info(), bound_playbook=None)
    assert o["show"] is False
    assert o["turns"] == 0
    assert o["reason"], "every verdict carries a reason the operator can read"


def test_a_seeded_conversation_is_offered_the_refinement_not_a_duplicate():
    """A conversation seeded FROM a playbook already has the right target: PUT refines it.
    Offering POST would grow the corpus with near-duplicates, which is the opposite of the
    decision's intent."""
    o = offer(_info(_run(30)), bound_playbook="research-and-report")
    assert o["show"] is True
    assert o["mode"] == "update"
    assert "research-and-report" in o["reason"]


def test_the_newest_finished_run_is_the_one_measured():
    """A conversation accumulates runs (one per reply). The offer reads the newest run that
    carries a summary — a fresh live reply on top of finished work must not hide the offer."""
    o = offer(_info(_run(3, summary=""), _run(25)), bound_playbook=None)
    assert o["show"] is True
    assert o["turns"] == 25


def test_the_threshold_is_reported_so_a_reader_can_check_the_verdict():
    for turns in (0, 1, SUBSTANTIAL_TURNS - 1, SUBSTANTIAL_TURNS, SUBSTANTIAL_TURNS + 100):
        o = offer(_info(_run(turns)), bound_playbook=None)
        assert o["threshold"] == SUBSTANTIAL_TURNS
        assert o["turns"] == turns
        assert o["show"] is (turns >= SUBSTANTIAL_TURNS)
