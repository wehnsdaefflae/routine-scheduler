"""F793: a live row some SETTLED report's prose claimed to dispose of — the third way work is
lost from this ledger.

F770 shipped the predicate (`report_threads.undisposed_claims`) and gave it ONE reader: the
filing observation (`engine/admin_handlers.py`), which warns and deliberately does not refuse.
So the claim is evaluated once, at filing time, in front of the one party who already believes
the row is closed — and never again. A run that does not act on its own observation strands the
row exactly as before: it stays open in the ledger and invisible to everyone, because the report
that was supposed to dispose of it reads as settled and nobody is looking behind it.

R2886 is the specimen and it is the reason the fix could not reach its own motivating row: it was
filed 2026-10-24, the detector shipped 2026-10-25. It opened "Closing R2313. Both halves disposed
of", carried `answers: "R2743"` + `closes: true` + no `settles`, and R2313 was still the oldest
live row on the instance 20 days later.

The narrowing is deliberate and tested below: only a SETTLED claimant counts. While the claiming
report is itself open its thread is alive, the row is in somebody's worklist, and the filing
warning is still in front of the author who can add the field. The stall becomes permanent at the
moment the claimant reads as settled.
"""
from __future__ import annotations

import json

# The R2886 / R2313 pair, as `reports.read_reports` folds it (D177: the ROW keeps the detail
# whole, so the prose a settled report claimed with is still on disk).
CLAIMED = {"id": "R2313", "ts": "2026-10-07T18:10:00+02:00",
           "routine": "c-20261007-150942", "target": "scheduler-improvement-research",
           "title": "create_routine cannot REVISE an existing routine",
           "detail": "Two findings, filed together.",
           "delivered": {"ts": "2026-10-10T09:00:00+02:00",
                         "run_id": "scheduler-improvement-research:20261010-010003"}}
CLAIMANT = {"id": "R2886", "ts": "2026-10-24T03:03:10+02:00",
            "routine": "scheduler-improvement-research", "target": "self-audit",
            "title": "R2313 settled: finding 1 is answered by D198 and parked",
            "detail": "Closing R2313. Both halves disposed of, so nothing of it should be "
                      "re-derived as an open research question.",
            "answers": "R2743", "closes": True}


def test_a_settled_reports_prose_claim_on_a_still_live_row_is_flagged():
    """The whole finding, in one row of evidence: R2886 reads settled (`closes: true`), its
    opening words say it closed R2313, its fields name only R2743 — and R2313 is live."""
    from rsched.readmodels.prose_closures import find

    found = find([CLAIMED, CLAIMANT])
    assert len(found) == 1, found
    flag = found[0]
    assert flag["kind"] == "prose-closure"
    assert flag["id"] == "R2313"                 # the row that is still live
    assert flag["claimed_by"] == "R2886"         # the settled report whose prose claimed it
    assert flag["claimed_by_routine"] == "scheduler-improvement-research"
    # only the routine a row was DELIVERED to can dispose of it, so the flag names it: without
    # the owner the reader cannot act, and the engine refuses anyone else's `settles`
    assert flag["owner"] == "scheduler-improvement-research"
    assert flag["title"] == CLAIMED["title"]     # what the live row is about
    assert "R2313" in flag["claim"]              # the claiming words themselves, for judgement


def test_the_claim_is_quoted_from_the_text_that_was_judged():
    """The quote is the title or the detail's OPENING SENTENCE — exactly the text
    `undisposed_claims` reads — so the banner never shows a reader prose the check did not
    judge, and never hides the prose it did.
    """
    from rsched.readmodels.prose_closures import find

    in_detail = {**CLAIMANT, "title": "A note on the research queue"}
    assert find([CLAIMED, in_detail])[0]["claim"].startswith("Closing R2313.")
    in_title = {**CLAIMANT, "title": "Closing R2313 — both halves disposed of",
                "detail": "Argued at length below, with the evidence from R2743."}
    assert find([CLAIMED, in_title])[0]["claim"] == "Closing R2313 — both halves disposed of"


def test_a_claim_the_fields_actually_carry_is_not_flagged():
    """The accurate closure must stay silent, or the surface fires on every well-written one
    and is ignored within a week. `settles` disposes of the row; the prose is then true.
    """
    from rsched.readmodels.prose_closures import find

    honest = {**CLAIMANT, "settles": ["R2313"]}
    assert find([CLAIMED, honest]) == []


def test_a_report_whose_prose_names_no_row_is_not_flagged():
    from rsched.readmodels.prose_closures import find

    quiet = {**CLAIMANT, "title": "The gate skips the browser stage",
             "detail": "Evidence: the run log. No reply needed."}
    assert find([CLAIMED, quiet]) == []


def test_a_claim_by_a_report_that_is_itself_still_open_is_not_flagged():
    """The narrowing. An OPEN claimant is a live thread: the row is on somebody's worklist, the
    filing warning is in front of the author who can still add the field, and the pair is being
    worked. This surface exists for the case where that has stopped being true.
    """
    from rsched.readmodels.prose_closures import find

    open_claimant = {k: v for k, v in CLAIMANT.items() if k not in ("closes", "answers")}
    assert find([CLAIMED, open_claimant]) == []


def test_a_retracted_claim_is_not_flagged():
    """Retraction unlinks a report on purpose — the same exclusion `orphans.find_undelivered`
    makes. A withdrawn claim claims nothing, and its row is left to the ordinary backlog.
    """
    from rsched.readmodels.prose_closures import find

    withdrawn = {**CLAIMANT, "retracted": {"ts": "2026-10-24T04:00:00+02:00"}}
    assert find([CLAIMED, withdrawn]) == []


def test_liveness_is_the_ledgers_own_reading():
    """One predicate, not a second opinion (the F768 discipline): the row's liveness is
    `report_threads.still_owed`, so a row ANOTHER report settled is not flagged here while the
    backlog counts it settled — two surfaces disagreeing about whether work is live is the
    defect this family of checks exists to end.
    """
    from rsched.readmodels.prose_closures import find

    settled_by_someone_else = {"id": "R3100", "ts": "2026-10-26T10:00:00+02:00",
                               "routine": "self-audit", "target": "routine-improver",
                               "title": "Disposing of the stragglers", "detail": "Done.",
                               "settles": ["R2313"], "closes": True}
    assert find([CLAIMED, CLAIMANT, settled_by_someone_else]) == []


def test_a_claim_on_a_row_the_ledger_does_not_have_is_not_flagged():
    """A citation of an id no row carries (a typo, a trimmed file) has nothing to strand:
    `still_owed` only answers for rows the ledger holds.
    """
    from rsched.readmodels.prose_closures import find

    assert find([{**CLAIMANT, "title": "Closing R9999", "detail": "Closing R9999."}]) == []


def test_each_pair_is_named_once_and_the_newest_claim_comes_first():
    from rsched.readmodels.prose_closures import find

    second = {"id": "R2400", "ts": "2026-10-09T10:00:00+02:00", "routine": "self-audit",
              "target": "routine-improver", "title": "a second live row", "detail": "x"}
    later = {"id": "R2990", "ts": "2026-10-27T10:00:00+02:00", "routine": "self-audit",
             "target": "routine-improver", "closes": True, "answers": "R2500",
             "title": "Closing R2400 and R2400 again", "detail": "Closing R2400."}
    found = find([CLAIMED, CLAIMANT, second, later])
    assert [(f["claimed_by"], f["id"]) for f in found] == [("R2990", "R2400"), ("R2886", "R2313")]


def test_load_reads_the_live_stream_and_survives_a_missing_one(tmp_path):
    from rsched.readmodels.prose_closures import load

    assert load(tmp_path) == []                            # no .control at all
    control = tmp_path / ".control"
    control.mkdir()
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (CLAIMED, CLAIMANT)), encoding="utf-8")
    assert [o["id"] for o in load(tmp_path)] == ["R2313"]


def test_the_api_surfaces_it(api_client, make_routine):
    """The SECOND READER the hand-over asked for: `GET /api/items/orphans`, beside the two
    existing loss mechanisms, so a row stranded by a prose-only closure is visible to someone
    who is not the author of the closure.
    """
    c, tmp = api_client
    make_routine(slug="self-audit")
    control = tmp / "routines" / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (CLAIMED, CLAIMANT)), encoding="utf-8")

    got = c.get("/api/items/orphans").json()
    stranded = [o for o in got if o.get("kind") == "prose-closure"]
    assert len(stranded) == 1, got
    assert stranded[0]["id"] == "R2313" and stranded[0]["claimed_by"] == "R2886"
    assert stranded[0]["owner"] == "scheduler-improvement-research"
