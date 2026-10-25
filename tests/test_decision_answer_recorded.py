"""An answer that arrived MID-RUN is recorded against its decision, not lost until the next
report happens to mention it (the operator's correction, 2026-10-25).

THE INCIDENT. He answered D210-D215 on the Decisions page between 02:07 and 02:17. Each answer
wrote both records `web/api_audit.write_feedback` writes: the durable marker in
`audit/decisions-answered.json`, and the queued `[AUDIT decision · D21x] selected: …` message
carrying his actual words. self-audit's report still listed all six `status: "open"` with an
empty `resolution`, because only its own next run writes that field — so every surface reading
the report alone showed six answered decisions as awaiting him, and this builder told him so in
a conversation. His instruction: *"also make the report record a resolution for an answer that
arrived mid-run"*.

The report file belongs to self-audit, so the ENGINE cannot write its `resolution` field. What
the engine owns is the item read model every surface reads, and that model recorded the answer
with NO WORDS BEHIND IT: a decision could read answered with an empty resolution, so no surface
could say what was decided. These tests pin that his words travel with the answer.

WHAT THESE TESTS DELIBERATELY DO NOT CHANGE — the item's `status`. It is the REPORT's
(self-audit's judgement, where `settled` means the item is dealt with) and the answer is an
OVERLAY the pages apply from `web/api_audit.answered_decisions`;
`test_api.py::test_items_page_reflects_answered_decision_after_consumption` pins that division.
A widening that let the marker flip `open` → `settled` was built on 2026-10-25, reddened that
test and was reverted the same run: D213 was answered and the work it authorized (F756) is
still unshipped, so `settled` would have misreported it.
"""
from __future__ import annotations

import json
from pathlib import Path

from rsched.readmodels import items, memo

ANSWER_TEXT = ("Two meanings, recorded: the engine refuses for calls IT chose to make and "
               "withholds for calls the RUN chose — ship F756 and write that distinction "
               "into both sites so it reads as deliberate")


def _audit(tmp_path: Path, *, decisions: list[dict], answered: dict,
           queued: list[tuple[str, str]] = ()) -> Path:
    """A self-audit routine dir: its report, its durable markers, and queued answers."""
    rdir = tmp_path / "self-audit"
    (rdir / "audit").mkdir(parents=True, exist_ok=True)
    (rdir / "inbox").mkdir(parents=True, exist_ok=True)
    (rdir / "audit" / "report.json").write_text(json.dumps({
        "schema": 1, "run_id": "self-audit:20261025-012019",
        "generated": "2026-10-25T02:40:00+02:00",
        "summary": "", "findings": [], "decisions": decisions}), encoding="utf-8")
    (rdir / "audit" / "decisions-answered.json").write_text(json.dumps(answered),
                                                            encoding="utf-8")
    for i, (did, words) in enumerate(queued):
        (rdir / "inbox" / f"msg-2026-10-25T0207{i:02d}+0100-feed{i}.json").write_text(
            json.dumps({"text": f"[AUDIT decision · {did}] selected: {words}",
                        "ts": f"2026-10-25T02:07:{i:02d}+01:00", "via": "web-audit",
                        "kind": "decision", "target": did, "choice": words, "raw": ""}),
            encoding="utf-8")
    memo.reset()
    return rdir


def _item(rdir: Path, item_id: str) -> dict:
    built = items._build(rdir, rdir.parent)
    return next(i for i in built["items"] if i["id"] == item_id)


def test_an_answered_decision_carries_the_operators_words_as_its_resolution(tmp_path):
    """The incident itself: the report says open with an empty resolution, his answer is in
    the inbox, and every surface read the report alone."""
    rdir = _audit(
        tmp_path,
        decisions=[{"id": "D213", "status": "open", "title": "When you DECLINE a secret…",
                    "detail": "F756 is built and uncommitted on this choice.",
                    "options": ["withhold", "refuse", "both", "leave as-is"],
                    "resolution": ""}],
        answered={"D213": "2026-10-25T02:10:47+01:00"},
        queued=[("D213", ANSWER_TEXT)])

    item = _item(rdir, "D213")
    assert item["resolution"], "an answered decision with an empty resolution records nothing"
    assert "ship F756" in item["resolution"], item["resolution"]
    # The STATUS stays the report's: D213 is answered and the work it authorized (F756) is
    # not shipped, so `settled` would misreport it. `web/api_audit.answered_decisions` is the
    # overlay that says "he answered this", and `test_api.py::
    # test_items_page_reflects_answered_decision_after_consumption` pins that division.
    assert item["status"] == "open"


def test_the_marker_alone_still_settles_it_and_says_so(tmp_path):
    """A mid-run delivery consumes the queued message instantly, so the marker may be the only
    record left. The status must still flip, and the resolution must say the answer exists
    rather than inventing words nobody wrote.
    """
    rdir = _audit(
        tmp_path,
        decisions=[{"id": "D214", "status": "open", "title": "Which language…",
                    "detail": "", "options": ["a", "b", "c", "d"], "resolution": ""}],
        answered={"D214": "2026-10-25T02:08:46+01:00"})

    item = _item(rdir, "D214")
    assert item["status"] == "open"             # still the report's, as above
    assert "2026-10-25T02:08:46+01:00" in item["resolution"], item["resolution"]


def test_a_resolution_the_report_wrote_is_never_overwritten(tmp_path):
    """Once self-audit records the resolution itself, the report is the authority: the read
    model must not replace its considered wording with a queued message's text.
    """
    rdir = _audit(
        tmp_path,
        decisions=[{"id": "D215", "status": "settled", "title": "Numbered prose…",
                    "detail": "", "options": ["a", "b"],
                    "resolution": "leave as-is — the reader-side fix is enough"}],
        answered={"D215": "2026-10-25T02:07:20+01:00"},
        queued=[("D215", "something a later edit superseded")])

    item = _item(rdir, "D215")
    assert item["resolution"] == "leave as-is — the reader-side fix is enough"


def test_an_unanswered_decision_is_untouched(tmp_path):
    """The cheap read must stay silent on a decision nobody has answered — otherwise every
    open decision grows a fake resolution."""
    rdir = _audit(
        tmp_path,
        decisions=[{"id": "D299", "status": "open", "title": "Still open",
                    "detail": "", "options": ["a", "b"], "resolution": ""}],
        answered={})

    item = _item(rdir, "D299")
    assert item["status"] == "open" and item["resolution"] == ""
