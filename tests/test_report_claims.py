"""F770: a report whose PROSE says it closes a row, while no field disposes of that row.

R2886 opened with "Closing R2313. Both halves disposed of" and argued it for paragraphs. Its
fields carried `answers: "R2743"` and `closes: true` and NO `settles` — so it closed its own
chase and left R2313 exactly where it was: open, and from then on the oldest live row on the
instance, which the backlog vitals line kept reporting for a row both its sender and its reader
believed was disposed of.

The asymmetry is what makes it durable: the sender reads its own prose and is satisfied, while
the recipient sees a row still live and cannot close it (the engine correctly refuses — a row is
disposed of only for the routine it was delivered TO). Nothing compared a report's TEXT against
the ids its fields dispose of, so the gap between "said" and "did" was invisible from both sides
at once. The engine says so on filing, where the author is the only one who can still fix it.
"""
from __future__ import annotations

import pytest

from rsched.report_threads import undisposed_claims

DISPOSITION_CLAIMS = [
    ("Closing R2313. Both halves disposed of", ["R2313"]),
    ("R2313 is closed by this", ["R2313"]),
    ("Settled: R900 needed no code", ["R900"]),
    ("Disposing of R12 and R13 here", ["R12", "R13"]),
    ("This closes R44", ["R44"]),
]


@pytest.mark.parametrize(("title", "claimed"), DISPOSITION_CLAIMS)
def test_a_disposition_verb_beside_an_id_in_the_opening_sentence_is_a_claim(title, claimed):
    assert undisposed_claims(title=title, detail="", disposed=()) == claimed


def test_an_id_merely_cited_as_evidence_is_not_a_claim():
    """The hard half of the check is telling "closing R2313" apart from "R2313 is why I am
    writing", so the predicate is narrow on purpose: a disposition VERB has to stand beside
    the id. Citing a row, quoting it, or building on it claims nothing.
    """
    for text in ("Measured while building R2313",
                 "R2313 is why I am writing",
                 "The evidence is in R2313 and R2400",
                 "Follow-up to R2313: the second half",
                 "R2313's premise is stale"):
        assert undisposed_claims(title=text, detail="", disposed=()) == [], text


def test_a_claim_the_fields_actually_carry_is_not_flagged():
    """`answers`, `settles` and `supersedes` are the three fields that dispose of a row. An id
    in any of them is DONE, so saying so in prose is accurate and must stay silent — otherwise
    the check fires on every well-written closure and is turned off within a week.
    """
    assert undisposed_claims(title="Closing R2313", detail="", disposed=("R2313",)) == []
    assert undisposed_claims(title="Closing R2313 and R900", detail="",
                             disposed=("r2313", "R900")) == []
    # the real R2886 shape: it answered one row and claimed another in prose
    assert undisposed_claims(title="Closing R2313. Both halves disposed of",
                             detail="Both are settled.", disposed=("R2743",)) == ["R2313"]


def test_only_the_opening_sentence_of_the_detail_counts():
    """The cheap version the row asks for: the FIRST sentence. A report body argues about rows
    for paragraphs — "F324 closed without naming it", "R2313 was closed in error" — and reading
    all of it would flag the evidence of every report that discusses the ledger at all.
    """
    detail = "This is the fix you asked for. It also closes R77, by the way."
    assert undisposed_claims(title="A fix for the gate", detail=detail, disposed=()) == []
    opener = "Closing R77. The rest is detail. And R88 is closed too."
    assert undisposed_claims(title="A fix", detail=opener, disposed=()) == ["R77"]


def test_an_id_claimed_twice_is_named_once():
    assert undisposed_claims(title="Closing R5", detail="Closing R5 as well.",
                             disposed=()) == ["R5"]


def test_nothing_to_say_about_a_report_that_names_no_row():
    assert undisposed_claims(title="The gate skips the browser stage", detail="Evidence: …",
                             disposed=()) == []


def test_the_filer_is_told_in_the_observation_while_it_can_still_fix_it(tmp_path):
    """The predicate has to reach the AUTHOR, in the turn he can still add the field: the
    recipient sees a live row and may not close it, and nobody else compares the two. So the
    filing observation says it — and does not refuse, because the prose may be a citation this
    check reads as a claim.
    """
    from rsched.engine.admin_handlers import handle_report
    from rsched.engine.observations import format_observation
    from test_report_disposal import _deliver, _loop

    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    improver, audit = _loop(home, "routine-improver"), _loop(home, "self-audit")

    # two real exchanges: the improver asks self-audit twice, and self-audit drains both
    handle_report(improver, {"target": "self-audit", "title": "ordinary work"})
    handle_report(improver, {"target": "self-audit", "title": "more work"})
    _deliver(home, "self-audit")

    # R2886's shape: the reply answers ONE row and claims another in its opening words
    claimed = handle_report(audit, {"target": "routine-improver", "answers": "R1",
                                    "closes": True,
                                    "title": "Closing R2313. Both halves disposed of",
                                    "detail": "Argued at length below."})
    assert claimed["filed"] and claimed["claimed_not_disposed"] == ["R2313"]
    rendered = format_observation(claimed)
    assert "R2313" in rendered
    assert "`answers`, `settles` and `supersedes` name none of them" in rendered
    assert "that row stays" in rendered              # singular, for one row

    # an ACCURATE closure says nothing: the id it names is the id its field disposes of
    accurate = handle_report(audit, {"target": "routine-improver", "answers": "R2",
                                     "closes": True, "title": "Closing R2: built and shipped"})
    assert accurate["filed"] and "claimed_not_disposed" not in accurate
    assert "name none of them" not in format_observation(accurate)
