"""D175: the conversation finish flow OFFERS a playbook when the conversation did real work."""

from __future__ import annotations

from types import SimpleNamespace

from rsched.web.playbook_offer import SUBSTANTIAL_TURNS, offer


def _info(*runs):
    """A RoutineInfo-shaped stand-in: `runs` newest-first, as the registry yields them."""
    return SimpleNamespace(runs=list(runs))


def _run(turn: int, summary: str = "did the thing", *, final: bool | None = None,
         state: str = "finished") -> SimpleNamespace:
    """A RunInfo-shaped stand-in. `final` is the reply's own declaration (registry.RunInfo
    reads it off status.json) and `state` is whether anything is still happening — the two
    halves D307's second reason needs."""
    return SimpleNamespace(turn=turn, summary=summary, final=final, state=state)


def test_a_conversation_that_did_real_work_is_offered_a_playbook():
    o = offer(_info(_run(SUBSTANTIAL_TURNS)), bound_playbook=None)
    assert o["show"] is True
    assert o["mode"] == "save"
    assert str(SUBSTANTIAL_TURNS) in o["reason"]
    assert "playbook" in o["reason"]
    # 0.443.1: the console leads with "This conversation did real work — ", so a reason
    # beginning "N turns of work" rendered "did real work — 23 turns of work —". The two
    # halves live in different files and each read fine alone; only the render showed it.
    # This stays a blocklist of ONE, and cannot be more: this module imports playbook_offer and
    # has no access to the console's lead-in. The guard on the COMPOSED sentence (one sentence,
    # one "?", no word repeated across the join) is the browser case
    # tests/ui/test_playbook_offer_sentence.py, which renders the row — R3645.
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


def test_a_finished_conversation_is_offered_a_playbook_however_short_it_was():
    """D307's SECOND reason. A reply that declares itself final has ENDED the conversation —
    the procedure is whole and nothing more is coming — so the offer does not wait for the turn
    count. Three turns is below `SUBSTANTIAL_TURNS`, which is what makes this case unable to
    pass on the first reason."""
    o = offer(_info(_run(3, final=True)), bound_playbook=None)
    assert o["show"] is True
    assert o["why"] == "final", "the console renders a final offer in a different place"
    assert o["mode"] == "save"
    assert "playbook" in o["reason"]


def test_a_live_conversation_is_not_called_done_however_it_declared_itself():
    """The state half of the predicate, and the half a wrong implementation drops: a reply that
    declared itself final and was then resumed by a further message is WORKING again, so the
    conversation has not ended. Its turn count is over the bar, so the composer's offer still
    stands — which is exactly the mix-up this case pins: `show` for the OTHER reason."""
    o = offer(_info(_run(30, final=True, state="running")), bound_playbook=None)
    assert o["why"] == "turns", "a live reply's `final: true` must not read as an ending"
    assert o["show"] is True


def test_a_reply_that_handed_back_with_work_open_is_not_an_ending():
    """`final: false` is a real declaration — the reply handed back with goals still open — and
    it must not be read as one. Below the turn bar, so nothing is offered at all."""
    o = offer(_info(_run(3, final=False)), bound_playbook=None)
    assert o["show"] is False
    assert o["why"] == ""


def test_a_finished_conversation_seeded_from_a_playbook_is_offered_the_refinement():
    o = offer(_info(_run(4, final=True)), bound_playbook="research-and-report")
    assert o["show"] is True and o["why"] == "final"
    assert o["mode"] == "update"
    assert "research-and-report" in o["reason"]


def test_the_two_reasons_never_fire_at_once():
    """One predicate, two surfaces, exactly one ask: a conversation that satisfies BOTH reasons
    is offered at its ending, not in two places. `why` is what the console reads to decide, so
    it is single-valued and the stronger reason wins."""
    o = offer(_info(_run(SUBSTANTIAL_TURNS + 10, final=True)), bound_playbook=None)
    assert o["show"] is True
    assert o["why"] == "final"


def test_a_substantial_conversation_still_says_which_reason_fired():
    """The composer's offer is unchanged by D307 except for carrying its own `why` — without it
    the console could not tell the two apart and would render both."""
    o = offer(_info(_run(SUBSTANTIAL_TURNS)), bound_playbook=None)
    assert o["show"] is True
    assert o["why"] == "turns"


def test_the_threshold_is_reported_so_a_reader_can_check_the_verdict():
    for turns in (0, 1, SUBSTANTIAL_TURNS - 1, SUBSTANTIAL_TURNS, SUBSTANTIAL_TURNS + 100):
        o = offer(_info(_run(turns)), bound_playbook=None)
        assert o["threshold"] == SUBSTANTIAL_TURNS
        assert o["turns"] == turns
        assert o["show"] is (turns >= SUBSTANTIAL_TURNS)
