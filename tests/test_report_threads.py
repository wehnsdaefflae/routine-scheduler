"""The FOLD and the OPEN-THREAD CAP — one thread per problem-class per owner.

Two defects with one shape, both fixed by giving the ledger an operation it never had
(0.345.0, docs/items.md § Reports):

- **F492** — routing a row to its owner produced a report that merely NAMED the original, so
  the original kept its empty `target`, the next triage pass called it untriaged, and routed it
  again. Six rows were routed twice inside two days.
- **D110** — the `problem-routing` rule told every holder to "add your evidence to the OLDEST
  open one rather than opening another", which the append-only ledger could not do. Live
  reports went 28 → 50 in the eleven days after that shipped.

`supersedes` is the operation; the cap is what makes a run reach for it. They are tested
together because neither is defensible alone: a cap with no way to consolidate loses findings,
and a fold nothing pushes a run toward goes unused.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched.engine.actions import validate_action
from rsched.engine.admin_handlers import handle_report
from rsched.engine.inbox import drain_messages
from rsched.engine.observations import format_observation
from rsched.readmodels import items
from rsched.report_threads import OPEN_THREAD_CAP, open_threads, supersedable
from rsched.reports import read_reports, reports_path, retract_report, stamp_delivered


def _routine(home: Path, slug: str) -> Path:
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    return d


def _loop(home: Path, slug: str, *, owed: list[str] | None = None):
    _routine(home, slug)
    return SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home), routine=SimpleNamespace(slug=slug),
        run_id=f"{slug}:20260915-120000", reports_open=list(owed or [])))


def _status(home: Path) -> dict[str, str]:
    audit = home / "self-audit"
    audit.mkdir(parents=True, exist_ok=True)
    built = items._build(*items.source_paths(audit, home))
    return {i["id"]: i["status"] for i in built["items"]}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "routines"
    h.mkdir(parents=True)
    (h / ".control").mkdir()
    for slug in ("self-audit", "global-utils-review", "freelance-radar"):
        _routine(h, slug)
    return h


# ---- the fold ---------------------------------------------------------------------------


def test_a_routed_row_leaves_triage_and_settles_with_its_carrier(home):
    """F492 end to end: the row a hand-off takes over stops being its own open item."""
    radar = _loop(home, "freelance-radar")
    handle_report(radar, {"title": "web-request clobbers --out on a 403"})
    assert _status(home)["R1"] == "open"                       # untargeted: waiting for triage

    audit = _loop(home, "self-audit")
    out = handle_report(audit, {"target": "global-utils-review", "title": "one util defect",
                                "supersedes": ["R1"]})
    assert out["supersedes"] == ["R1"]
    # the original now reads the carrier's state, not its own
    assert _status(home)["R1"] == _status(home)["R2"] == "open"

    guv = _loop(home, "global-utils-review")
    stamp_delivered(home, drain_messages(home / "global-utils-review", home / "consumed"),
                    run_id="global-utils-review:20260915-130000")
    assert _status(home)["R1"] == "in_progress"                # picked up, through the carrier

    guv.ctx.reports_open = ["R2"]
    handle_report(guv, {"target": "self-audit", "title": "fixed", "answers": "R2",
                        "closes": True})
    settled = _status(home)
    assert settled["R2"] == "settled"
    assert settled["R1"] == "settled", "a folded row settles when the thread it joined does"


def test_the_fold_is_recorded_on_the_row_and_reaches_the_read_model(home):
    handle_report(_loop(home, "freelance-radar"), {"title": "a"})
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review", "title": "b",
                                              "supersedes": ["R1"]})
    row = {r["id"]: r for r in read_reports(reports_path(home))}["R1"]
    assert row["superseded"]["by"] == "R2"
    assert row["superseded"]["to"] == "global-utils-review"
    built = {i["id"]: i for i in items._build(*items.source_paths(home / "self-audit", home))["items"]}
    assert built["R1"]["superseded"]["by"] == "R2"
    assert built["R2"]["supersedes"] == ["R1"]
    # …and it now reads as OWNED by the carrier's target: a folded row that still said
    # "no target" would be re-routed by the very triage query this fold exists to satisfy
    assert built["R1"]["to"] == "global-utils-review"


def test_the_first_fold_wins(home):
    """A row belongs to exactly one thread — the ledger's re-routing defence.

    Without this, the second triage pass that named the same rows (R1558 after R1525) would
    move them into its own thread and the first carrier would be answering for nothing.
    """
    handle_report(_loop(home, "freelance-radar"), {"title": "a"})
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review", "title": "first",
                                              "supersedes": ["R1"]})
    second = handle_report(_loop(home, "self-audit"),
                           {"target": "global-utils-review", "title": "second",
                            "supersedes": ["R1"], "answers": "R2"})
    assert second.get("unusable") == ["R1"], "already folded — refused rather than stolen"
    row = {r["id"]: r for r in read_reports(reports_path(home))}["R1"]
    assert row["superseded"]["by"] == "R2"


def test_a_fold_chain_resolves_to_the_live_thread(home):
    """A carrier can itself be taken over later; the rows it carried follow to the END of the
    chain, never to an intermediate nobody is working any more.
    """
    handle_report(_loop(home, "freelance-radar"), {"title": "original"})
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "first carrier", "supersedes": ["R1"]})
    handle_report(_loop(home, "self-audit"), {"target": "global-utils-review",
                                              "title": "consolidated", "supersedes": ["R2"]})
    guv = _loop(home, "global-utils-review", owed=["R3"])
    handle_report(guv, {"target": "self-audit", "title": "all fixed", "answers": "R3",
                        "closes": True})
    st = _status(home)
    assert st["R3"] == "settled"
    assert st["R2"] == "settled", "one link down"
    assert st["R1"] == "settled", "two links down — the chain, not just the last hop"


def test_a_report_cannot_both_answer_and_absorb_the_same_row(home):
    """Answering ENDS that thread; taking it over CONTINUES it here. Both at once would let
    the fold win silently while the run believed it had closed the row.
    """
    problems = validate_action({"kind": "report", "say": "s", "title": "t",
                                "target": "global-utils-review", "answers": "R1",
                                "supersedes": ["R1"]})
    assert any("cannot be both" in p for p in problems)


def test_unusable_ids_refuse_the_whole_report(home):
    """Naming them beats folding what it can: a run told "3 of 5 taken over" has to work out
    which two it still owns, which is the bookkeeping the field exists to remove.
    """
    handle_report(_loop(home, "freelance-radar"), {"title": "real"})
    out = handle_report(_loop(home, "self-audit"),
                        {"target": "global-utils-review", "title": "x",
                         "supersedes": ["R1", "R99"]})
    assert out["unusable"] == ["R99"]
    assert "filed" not in out
    assert len(read_reports(reports_path(home))) == 1, "nothing was written"
    rendered = format_observation(out)
    assert "REFUSED" in rendered and "R99" in rendered


def test_a_retracted_row_cannot_be_folded(home):
    audit = _loop(home, "self-audit")
    handle_report(audit, {"target": "global-utils-review", "title": "withdraw me"})
    retract_report(home, "R1")
    out = handle_report(audit, {"target": "global-utils-review", "title": "x",
                                "supersedes": ["R1"]})
    assert out["unusable"] == ["R1"]


def test_supersedes_needs_a_target(home):
    """Folding hands rows to an owner. With no target there is no owner to hand them to."""
    problems = validate_action({"kind": "report", "say": "s", "title": "t",
                                "supersedes": ["R1"]})
    assert any("needs 'target'" in p for p in problems)


def test_supersedes_rejects_things_that_are_not_report_ids(home):
    problems = validate_action({"kind": "report", "say": "s", "title": "t",
                                "target": "global-utils-review",
                                "supersedes": ["F12", "not-an-id"]})
    assert any("takes report ids like R123" in p for p in problems)


# ---- the cap ----------------------------------------------------------------------------


def _fill_to_cap(home) -> None:
    for n in range(OPEN_THREAD_CAP):
        handle_report(_loop(home, "self-audit"),
                      {"target": "global-utils-review", "title": f"thread {n}"})


def test_the_cap_refuses_the_next_parallel_thread_and_names_the_open_ids(home):
    _fill_to_cap(home)
    out = handle_report(_loop(home, "self-audit"),
                        {"target": "global-utils-review", "title": "one more"})
    assert out["thread_cap"] == OPEN_THREAD_CAP
    assert out["open_to_target"] == ["R1", "R2", "R3"]
    assert out["oldest"] == "R1", "oldest first — that is the one to fold into"
    assert len(read_reports(reports_path(home))) == OPEN_THREAD_CAP, "nothing was filed"
    rendered = format_observation(out)
    assert "REFUSED" in rendered and "R1, R2, R3" in rendered
    assert "supersedes" in rendered, "a refusal must carry the way through"


def test_the_cap_is_per_sender_and_per_owner(home):
    _fill_to_cap(home)
    # a DIFFERENT owner is a different queue
    assert "thread_cap" not in handle_report(
        _loop(home, "self-audit"), {"target": "freelance-radar", "title": "elsewhere"})
    # a DIFFERENT sender is not answerable for self-audit's concentration
    assert "thread_cap" not in handle_report(
        _loop(home, "freelance-radar"), {"target": "global-utils-review", "title": "mine"})


def test_a_reply_and_a_fold_are_never_capped(home):
    """Both REDUCE the thread count. Capping the way out is how a cap loses a finding."""
    _fill_to_cap(home)
    audit = _loop(home, "self-audit")
    assert handle_report(audit, {"target": "global-utils-review", "title": "answering",
                                 "answers": "R1"})["filed"]
    _fill_to_cap(home)                       # back up to the cap (R1 is answered, R4 added)
    folded = handle_report(audit, {"target": "global-utils-review", "title": "consolidating",
                                   "supersedes": ["R2", "R3"]})
    assert folded["filed"] and folded["supersedes"] == ["R2", "R3"]


def test_an_untargeted_report_is_never_capped(home):
    """Triage has no owner to overload, and a run that cannot name one must still be able to
    say what it found — that is the whole point of the unaddressed half of the channel.
    """
    for n in range(OPEN_THREAD_CAP + 2):
        assert handle_report(_loop(home, "self-audit"), {"title": f"triage {n}"})["filed"]


def test_folding_frees_the_queue_again(home):
    """The loop the cap is meant to create: refused → fold → the next thread is allowed."""
    _fill_to_cap(home)
    audit = _loop(home, "self-audit")
    assert handle_report(audit, {"target": "global-utils-review",
                                 "title": "blocked"})["thread_cap"]
    handle_report(audit, {"target": "global-utils-review", "title": "all three at once",
                          "supersedes": ["R1", "R2", "R3"]})
    # one thread open now, not three — so a genuinely new problem gets through
    assert handle_report(audit, {"target": "global-utils-review",
                                 "title": "a new class"})["filed"]


# ---- the readers the write path depends on ----------------------------------------------


def test_open_threads_excludes_every_form_of_closed(home):
    rows = [
        {"id": "R1", "routine": "a", "target": "b"},
        {"id": "R2", "routine": "a", "target": "b", "retracted": {"ts": "x"}},
        {"id": "R3", "routine": "a", "target": "b", "superseded": {"by": "R9"}},
        {"id": "R4", "routine": "a", "target": "b", "answers": "R0", "closes": True},
        {"id": "R5", "routine": "a", "target": "b"},
        {"id": "R6", "routine": "a", "target": "b", "answers": "R5"},
        {"id": "R7", "routine": "a", "target": "OTHER"},
        {"id": "R8", "routine": "OTHER", "target": "b"},
    ]
    # R1 alone: R2 retracted, R3 folded, R4 born settled, R5 answered by R6, R6 answers (open),
    # R7/R8 are a different pair of ends
    assert open_threads(rows, routine="a", target="b") == ["R1", "R6"]


def test_supersedable_splits_and_keeps_the_callers_order(home):
    rows = [{"id": "R1"}, {"id": "R2", "retracted": {"ts": "x"}},
            {"id": "R3", "superseded": {"by": "R9"}}]
    ok, bad = supersedable(rows, ["r3", "R1", "R404", "R2"])
    assert ok == ["R1"]
    assert bad == ["R3", "R404", "R2"]
