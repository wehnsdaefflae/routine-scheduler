"""Who may DISPOSE of a report row — `answers` and `settles` belong to the recipient (D182).

The engine's own prose says `answers` is "the id of a report you RECEIVED", and the grammar of
`answers`/`settles`/`supersedes` was validated thoroughly — but never the RELATIONSHIP. So a row
left the ledger whenever any report named it, its own sender's included. Measured over the live
2,110-row ledger before this landed: 492 disposals were made by someone who was not the row's
owner, 317 of them of rows never routed to anyone, which means they left triage with nobody
having read them. The operator's own bug report (R2552) vanished that way 14 seconds after he
filed it, while its follow-up stayed visible.

The gate is a WRITE precondition, in the handler, where the author can be told what to do
instead. The read model is deliberately untouched: the rows already disposed of this way stay
settled, because re-deriving them would hand triage hundreds of rows at once — which is the
operator's own instruction with the fix ("migrate them as-is").
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import bare_routine
from rsched.engine.admin_handlers import handle_report
from rsched.engine.inbox import drain_messages
from rsched.engine.observations import format_observation
from rsched.readmodels import items
from rsched.report_threads import OPEN_THREAD_CAP, disposable, owner_of
from rsched.reports import read_reports, reports_path, stamp_delivered


def _loop(home: Path, slug: str, *, owed: list[str] | None = None):
    bare_routine(home, slug)
    return SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home), routine=SimpleNamespace(slug=slug),
        run_id=f"{slug}:20260915-120000", reports_open=list(owed or [])))


def _rows(home: Path) -> list[dict]:
    return read_reports(reports_path(home))


def _status(home: Path) -> dict[str, str]:
    audit = home / "self-audit"
    audit.mkdir(parents=True, exist_ok=True)
    return {i["id"]: i["status"] for i in items._build(audit, home)["items"]}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "routines"
    h.mkdir(parents=True)
    (h / ".control").mkdir()
    for slug in ("self-audit", "global-utils-review", "freelance-radar"):
        bare_routine(h, slug)
    return h


def _deliver(home: Path, slug: str) -> None:
    stamp_delivered(home, drain_messages(home / slug, home / "consumed"),
                    run_id=f"{slug}:20260915-130000")


# ---- the three shapes the decision names ------------------------------------------------


def test_the_recipient_settles_the_row_it_was_given(home):
    """The allowed shape, unchanged: the routine a row was DELIVERED to disposes of it."""
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "a util defect"})
    _deliver(home, "global-utils-review")
    guv = _loop(home, "global-utils-review", owed=["R1"])
    out = handle_report(guv, {"target": "self-audit", "title": "fixed", "answers": "R1",
                              "closes": True})
    assert out["filed"]
    assert _status(home)["R1"] == "settled"


def test_a_sender_cannot_retire_its_own_unread_report(home):
    """The measured defect: R2552 (untargeted, the operator's own) was settled by its
    14-second-later follow-up and vanished from every open view."""
    handle_report(_loop(home, "freelance-radar"), {"title": "web-request clobbers --out"})
    out = handle_report(_loop(home, "freelance-radar"),
                        {"title": "more on the same", "answers": "R1"})
    assert out.get("undeliverable_disposal") is True
    assert out["unrouted"] == ["R1"]
    assert "filed" not in out
    assert len(_rows(home)) == 1, "nothing was written"
    assert _status(home)["R1"] == "open", "it stays in triage, where it can still be read"


def test_a_third_routine_cannot_settle_someone_elses_row(home):
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "a util defect"})
    _deliver(home, "global-utils-review")
    out = handle_report(_loop(home, "freelance-radar"),
                        {"target": "self-audit", "title": "I'll take this", "answers": "R1"})
    assert out.get("undeliverable_disposal") is True
    assert out["elsewhere"] == [("R1", "global-utils-review")]
    assert _status(home)["R1"] == "in_progress", "still its owner's row"


def test_an_untargeted_row_must_be_routed_before_it_can_be_settled(home):
    """The operator's wording: "an untargeted row must be routed before it can be settled".
    Routing is the fold, and the owner then disposes of it — the loop end to end."""
    handle_report(_loop(home, "freelance-radar"), {"title": "a triage row"})
    audit = _loop(home, "self-audit")
    assert handle_report(audit, {"title": "cannot settle this", "settles": ["R1"]}
                         ).get("undeliverable_disposal") is True
    routed = handle_report(audit, {"target": "global-utils-review", "title": "yours",
                                   "supersedes": ["R1"]})
    assert routed["filed"]
    _deliver(home, "global-utils-review")
    guv = _loop(home, "global-utils-review", owed=["R2"])
    assert handle_report(guv, {"target": "self-audit", "title": "done", "answers": "R2",
                               "closes": True})["filed"]
    settled = _status(home)
    assert settled["R2"] == "settled" and settled["R1"] == "settled"


# ---- the two refusals stay distinguishable ----------------------------------------------


def test_the_two_refusals_say_different_things_and_name_different_moves(home):
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "owned elsewhere"})          # R1
    handle_report(_loop(home, "freelance-radar"), {"title": "nobody's yet"})         # R2
    out = handle_report(_loop(home, "freelance-radar"),
                        {"target": "self-audit", "title": "both at once",
                         "settles": ["R1", "R2"]})
    assert out["elsewhere"] == [("R1", "global-utils-review")]
    assert out["unrouted"] == ["R2"]
    rendered = format_observation(out)
    assert "REFUSED" in rendered
    assert "R1 → global-utils-review" in rendered         # whose it is
    assert "NOT ROUTED YET" in rendered and "R2" in rendered
    assert "supersedes" in rendered, "a refusal must carry the way through"
    assert "Nothing was filed" in rendered


# ---- the owner, through a fold chain -----------------------------------------------------


def test_a_folded_rows_owner_is_its_carriers_target(home):
    """A routed row belongs to the thread it joined, so the thread's owner disposes of it —
    otherwise the ordinary consolidate-then-reply pass would refuse itself."""
    handle_report(_loop(home, "freelance-radar"), {"title": "original"})              # R1
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "first carrier",
                                              "supersedes": ["R1"]})                  # R2
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "consolidated",
                                              "supersedes": ["R2"]})                  # R3
    rows = _rows(home)
    assert owner_of(rows, "R1") == "global-utils-review", "two links down the chain"
    assert owner_of(rows, "R3") == "global-utils-review"
    assert owner_of(rows, "R404") is None, "not in the ledger at all"
    _deliver(home, "global-utils-review")
    guv = _loop(home, "global-utils-review", owed=["R3"])
    assert handle_report(guv, {"target": "self-audit", "title": "all of it",
                               "settles": ["R1", "R3"], "closes": True})["filed"]


def test_disposable_splits_in_the_callers_order_and_names_each_id_once():
    rows = [{"id": "R1", "routine": "a", "target": "b"},
            {"id": "R2", "routine": "a"},
            {"id": "R3", "routine": "c", "target": "a"}]
    assert disposable(rows, ["R3", "r1", " R1 ", "R2", "R404"], replier="a") == (
        ["R3"], ["R1"], ["R2", "R404"])


# ---- what must NOT change ----------------------------------------------------------------


def test_rows_already_disposed_of_by_a_non_owner_stay_settled(home):
    """The operator's own note with the decision: 113 rows were settled by their own sender —
    reopening them would hand triage 49 unread rows at once. The gate is a WRITE precondition
    and the read model does not re-litigate history."""
    import json

    handle_report(_loop(home, "freelance-radar"), {"title": "historical row"})
    path = reports_path(home)
    with path.open("a", encoding="utf-8") as fh:        # as the old engine would have written it
        fh.write(json.dumps({"id": "R2", "ts": "2026-09-01T00:00:00+02:00",
                             "routine": "freelance-radar",
                             "run_id": "freelance-radar:20260901-000000",
                             "title": "its own follow-up", "detail": "",
                             "answers": "R1"}) + "\n")
    assert _status(home)["R1"] == "settled"
    assert owner_of(_rows(home), "R1") == "", "and it is still nobody's row"


def test_a_settling_reply_is_exempt_from_the_open_thread_cap(home):
    """The cap refuses a new parallel thread, never the way OUT of one — and after D182 the
    rows a reply settles are ones delivered TO it, so the exemption is tested on that shape.
    """
    audit = _loop(home, "self-audit")
    improver = _loop(home, "global-utils-review")
    for n in range(OPEN_THREAD_CAP):
        handle_report(audit, {"target": "global-utils-review", "title": f"thread {n}"})
        handle_report(improver, {"target": "self-audit", "title": f"mine {n}"})
    mine = [f"R{2 * n + 2}" for n in range(OPEN_THREAD_CAP)]

    obs = handle_report(audit, {"target": "global-utils-review", "title": "all of them are done",
                                "settles": mine, "closes": True})
    assert obs["filed"] is True and "thread_cap" not in obs
    status = _status(home)
    assert all(status[i] == "settled" for i in mine)
