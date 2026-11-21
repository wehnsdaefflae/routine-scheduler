"""The per-routine finish-verifier cascade (D302): who may be screened, at what probability,
and the three ways a finish still reaches the prose judge — pinned against the measurement the
operator's answer was given on (515 finish claims in the shadow stream, 2026-11-15 → 11-21).
"""

from __future__ import annotations

from rsched.engine import claim_screen


def test_the_six_routines_the_answer_names_are_the_screened_set():
    """His answer is the authority on WHO, so the table admits exactly those six."""
    assert set(claim_screen.MEASURED) == {
        "folder-reorg", "rules-review", "feeld-webui-maintainer", "moltbook-heartbeat",
        "config-optimizer", "fau"}
    assert all(claim_screen.screened(slug) for slug in claim_screen.MEASURED)


def test_the_three_routines_below_the_bar_keep_the_prose_judge():
    """Listed rather than merely omitted, so a later edit cannot promote one by forgetting."""
    for slug in ("tv-show-tracker-seedbox-manager", "global-utils-review", "freelance-hunter"):
        assert slug in claim_screen.EXCLUDED
        assert not claim_screen.screened(slug)


def test_a_routine_the_answer_does_not_name_keeps_the_prose_judge():
    """The answer covers six and three; the ones between 85% and 100%, the ones with too few
    pairs and every routine created since are the default case, and the default is the judge."""
    assert not claim_screen.screened("deficit-coach")          # 92.1%, not named
    assert not claim_screen.screened("a-routine-created-tomorrow")
    assert claim_screen.agreement("a-routine-created-tomorrow") is None


def test_the_threshold_is_the_measured_safe_floor_not_the_shadows_recording_one():
    """At the stream's recording threshold of 0.5 the screen passes 8 of the 11 claims the
    prose judge refuted (0.549 to 0.942); zero are missed from 0.95 up."""
    assert claim_screen.THRESHOLD >= 0.95


def test_a_routine_at_a_hundred_percent_over_too_few_pairs_is_not_screened():
    """Without the minimum-pairs rule a new routine's first three agreeing claims buy it a
    screen, which is agreement measured on nothing."""
    original = dict(claim_screen.MEASURED)
    try:
        claim_screen.MEASURED["three-agreeing-claims"] = (3, 3)
        assert not claim_screen.screened("three-agreeing-claims")
    finally:
        claim_screen.MEASURED.clear()
        claim_screen.MEASURED.update(original)


def test_a_routine_short_of_perfect_agreement_is_not_screened_even_if_listed():
    """The table's own entries are checked, not trusted: an entry whose figures stop saying
    100% stops being screened without anyone remembering to delete the line."""
    original = dict(claim_screen.MEASURED)
    try:
        claim_screen.MEASURED["folder-reorg"] = (25, 26)
        assert not claim_screen.screened("folder-reorg")
    finally:
        claim_screen.MEASURED.clear()
        claim_screen.MEASURED.update(original)


def test_the_judge_is_skipped_only_when_every_claim_clears_the_threshold():
    """The prose judge is ONE call over all the claims, so screening some of them saves
    nothing and loses the judge's view of the rest."""
    high = claim_screen.THRESHOLD + 0.01
    assert claim_screen.may_skip_judge("folder-reorg", [high, high, 1.0])
    assert not claim_screen.may_skip_judge("folder-reorg", [high, 0.6])


def test_a_claim_the_screen_could_not_answer_sends_the_finish_to_the_judge():
    """No decision model, a provider error or a malformed answer is a claim the screen did not
    cover — the cascade may be cheaper than the judge but never quieter than it."""
    high = claim_screen.THRESHOLD + 0.01
    assert not claim_screen.may_skip_judge("folder-reorg", [high, None])
    assert not claim_screen.may_skip_judge("folder-reorg", [])


def test_an_excluded_routine_is_never_skipped_however_confident_the_screen_is():
    assert not claim_screen.may_skip_judge("global-utils-review", [1.0, 1.0])


def test_every_measured_entry_carries_the_figures_it_claims_to_rest_on():
    """Each entry is traceable to the shadow stream; a bare slug would be an assertion."""
    for slug, pairs in {**claim_screen.MEASURED, **claim_screen.EXCLUDED}.items():
        agree, total = pairs
        assert 0 < agree <= total, slug
        assert claim_screen.agreement(slug) == agree / total
