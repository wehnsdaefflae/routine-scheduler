"""WHOSE regression is it — this routine's, or the host's? (F568)

The cost-trend alarm fired three times naming a routine for a host-wide outage it did not
cause (`llmsectest-weekday:20260927-060004`, `library-sync:20260928-000003`, F574). D181 tuned
the SENSITIVITY of that alarm (`BALLOON_RATIO` 1.5 → 2.7, pinned by tests/test_balloon_ratio.py);
this file pins the ATTRIBUTION half, which nothing touched: before a finding names a routine, the
same window is read across the FLEET, and a degradation shared by the routines that ran beside it
is reported as a condition of the HOST.

Two failures are possible here and both are worse than the bug:
1. stopping naming anyone — a real single-routine regression must still be named, so
   `test_an_isolated_regression_is_still_named` and `test_half_the_fleet_is_not_a_majority`
   are as load-bearing as the host case;
2. a false host verdict on a quiet night — two routines at 3 a.m. are not a fleet, which is
   what HOST_MIN_PEERS is for, and a window too thin to tell the cases apart says SO
   (`test_too_few_comparable_routines_says_so`) instead of guessing either way.

The numbers in `trend_attribution` are measured off this instance's own stream; the measurement
is quoted in that module's docstring beside each constant.
"""

from __future__ import annotations

import json

from rsched.health_events import log_health_event
from rsched.readmodels.run_health import REGRESSION_WINDOW
from rsched.readmodels.trend_attribution import (
    HOST_MIN_PEERS,
    HOST_SHARE,
    attribute,
    event_payload,
)

CHEAP = 1_000_000
DEAR = 20_000_000          # 20× CHEAP: clears BALLOON_RATIO and VOLUME_FLOOR together


def _day(n: int) -> str:
    """One stamp per day, with an offset — `ids.now_iso` writes local time with one."""
    return f"2026-09-{n:02d}T02:00:00+02:00"


def _run(slug: str, day: int, *, status: str = "ok", turns: int = 10,
         volume: int | None = CHEAP) -> dict:
    return {"routine": slug, "run_id": f"{slug}:2026092{day % 10}-0200{day:02d}",
            "ts": _day(day), "status": status, "turns": turns, "volume": volume, "depth": 0}


def _series(slug: str, days: list[int], **kw) -> list[dict]:
    return [_run(slug, d, **kw) for d in days]


# The subject: five cheap runs on days 1-5, five dear ones on days 11-15. Its flagged window
# is therefore the span 11→15, and that span is what every peer is judged over.
BASELINE_DAYS = [1, 2, 3, 4, 5]
WINDOW_DAYS = [11, 12, 13, 14, 15]
PEER_BEFORE = [6, 7, 8]            # ≥ MIN_RUNS of baseline, all before the span
PEER_INSIDE = [11, 12, 13]         # ≥ MIN_RUNS inside the span


def _subject(slug: str = "subject") -> list[dict]:
    return _series(slug, BASELINE_DAYS, volume=CHEAP) + _series(slug, WINDOW_DAYS, volume=DEAR)


def _peer(slug: str, *, degrades: bool) -> list[dict]:
    return (_series(slug, PEER_BEFORE, volume=CHEAP)
            + _series(slug, PEER_INSIDE, volume=DEAR if degrades else CHEAP))


def _fleet(degrading: int, steady: int, *, subject: str = "subject") -> list[dict]:
    runs = _subject(subject)
    for i in range(degrading):
        runs += _peer(f"degrader{i}", degrades=True)
    for i in range(steady):
        runs += _peer(f"steady{i}", degrades=False)
    return runs


def _attribute(degrading: int, steady: int, subject: str = "subject") -> dict:
    fleet = _fleet(degrading, steady, subject=subject)
    own = [r for r in fleet if r["routine"] == subject]
    return attribute(fleet, own, subject)


# ---- the host case: the finding F568 says must exist -----------------------------------


def test_a_degradation_shared_by_the_fleet_is_a_host_condition():
    """THE case from F568: three other routines ran the same window and degraded with the
    subject. The finding is then about the HOST and says so, and it does not pin the outage
    on the routine whose run happened to read it.

    `llmsectest-weekday` and `library-sync` were each named for one of these on 2026-09-27/28;
    an operator read three accusations of three routines for one condition of the box.
    """
    att = _attribute(degrading=3, steady=1)
    assert att["scope"] == "host", att
    assert att["peers_compared"] == 4 and att["peers_degraded"] == 3
    assert att["share"] == 0.75
    # the wording the operator reads: about the host, not about a routine
    assert att["sentence"].startswith("host-wide:")
    assert "not one routine's regression" in att["sentence"]
    assert sorted(att["degraded_names"]) == ["degrader0", "degrader1", "degrader2", "subject"]


def test_a_host_finding_names_no_routine_at_all():
    """`backup_stale`'s idiom (health_events.py): an instance fact carries no `routine`.

    That is what stops the row reading as an accusation — and it is also what collapses the
    three rows into one, because `health_cooldown` keys its window on (event, routine) and
    every host row shares the same empty key whichever routine detected it.
    """
    att = _attribute(degrading=3, steady=1)
    routine, detail, fields = event_payload("subject", _trend_verdict(), att)
    assert routine == ""
    assert fields["scope"] == "host"
    assert fields["detected_on"] == "subject"
    assert detail.startswith("host-wide:")
    assert "median volume ballooned" in detail       # the reading is still in the row
    assert len(detail) <= 500


# ---- the other direction: a real regression must still be named ------------------------


def test_an_isolated_regression_is_still_named():
    """A fix that stops naming anyone is worse than the bug it replaces.

    Four comparable routines ran the same window and none degraded, so the degradation is
    this routine's own and the finding says that — with the fleet reading as its evidence.
    """
    att = _attribute(degrading=0, steady=4)
    assert att["scope"] == "routine", att
    assert att["peers_compared"] == 4 and att["peers_degraded"] == 0
    assert att["share"] == 0.0
    assert "this routine's own" in att["sentence"]
    routine, detail, fields = event_payload("subject", _trend_verdict(), att)
    assert routine == "subject"                      # the row still names it
    assert fields["scope"] == "routine"
    assert "median volume ballooned" in detail


def test_half_the_fleet_is_not_a_majority():
    """HOST_SHARE is a STRICT majority, and the boundary is where this kind of fix goes wrong.

    Measured on this instance's own stream (134 flagging moments, 217 actors, 1,738 depth-0
    runs, 2026-10-21): the share of comparable routines degrading alongside a flagged routine
    ran median 0.11, p90 0.36, p95 0.40, max 0.50. Exactly half has HAPPENED; more than half
    never has. So 2-of-4 must stay the routine's own finding, or the rule would rewrite real
    history as host conditions.
    """
    att = _attribute(degrading=2, steady=2)
    assert att["share"] == HOST_SHARE == 0.5
    assert att["scope"] == "routine", "a tie is not a majority — see the measurement"


# ---- the thin window: say so rather than guess -----------------------------------------


def test_too_few_comparable_routines_says_so():
    """An honest "cannot attribute, N routines in the window" beats a false accusation.

    This is the 2026-09-27/28 night itself: at those three firings exactly ONE other routine
    had comparable runs in the window, so neither verdict was available. 7 of this instance's
    134 flagging moments (5.2%) are this case.
    """
    att = _attribute(degrading=1, steady=0)
    assert att["peers_compared"] == 1 < HOST_MIN_PEERS
    assert att["scope"] == "unattributable", att
    assert "cannot attribute" in att["sentence"]
    assert "1 other routine" in att["sentence"]       # the N is in the words, not only a field
    routine, detail, fields = event_payload("subject", _trend_verdict(), att)
    # the reading is NOT suppressed: the degradation is real and still reported, under the
    # routine it was measured on — what is withheld is the claim about its cause.
    assert routine == "subject" and fields["scope"] == "unattributable"
    assert "median volume ballooned" in detail
    assert fields["peers_compared"] == 1


def test_a_routine_with_no_fleet_around_it_is_unattributable():
    """The hermetic case every unit test of the alarm produces: no other routine in the
    stream at all. One routine's stream cannot distinguish a host from a recipe."""
    att = _attribute(degrading=0, steady=0)
    assert att["scope"] == "unattributable" and att["peers_compared"] == 0
    assert "no other routine" in att["sentence"]


def test_a_peer_needs_comparable_runs_on_both_sides_to_vote():
    """A peer with one run in the window, or with no history before it, is not evidence —
    MIN_RUNS on both sides, the same floor `regression_flag` applies to the subject.

    Without this, a host verdict could rest on routines that never ran the window at all.
    """
    fleet = _subject()
    fleet += _series("thin-inside", PEER_BEFORE, volume=CHEAP) + _series(
        "thin-inside", [11], volume=DEAR)                     # 1 run inside the span
    fleet += _series("no-baseline", PEER_INSIDE, volume=DEAR)  # nothing before the span
    fleet += _peer("real", degrades=True)
    own = [r for r in fleet if r["routine"] == "subject"]
    att = attribute(fleet, own, "subject")
    assert att["peers_compared"] == 1 and att["peers_degraded"] == 1
    assert att["scope"] == "unattributable", "two non-comparable peers must not vote"


def test_the_subject_is_never_its_own_peer():
    """The subject's own runs are the thing being attributed, not a vote about it."""
    att = _attribute(degrading=3, steady=1)
    assert "subject" not in att["compared_names"]
    assert att["peers_compared"] == len(att["compared_names"])


def test_an_unstamped_run_cannot_place_a_window():
    """Stamps are read as INSTANTS and a row the stream could not stamp places nothing. A
    subject whose window carries no readable stamp is unattributable, never host."""
    own = _series("subject", BASELINE_DAYS, volume=CHEAP) + [
        {"routine": "subject", "run_id": "subject:x", "ts": "not a date", "status": "ok",
         "turns": 10, "volume": DEAR} for _ in range(REGRESSION_WINDOW)]
    att = attribute(own + _peer("degrader0", degrades=True), own, "subject")
    assert att["scope"] == "unattributable"


# ---- the three rows that started this ---------------------------------------------------


def test_one_host_condition_files_one_row_for_the_whole_fleet(tmp_path):
    """F568's complaint in one test: three routines detecting ONE host condition must not
    file three findings naming three routines.

    A host row carries no `routine`, so `health_cooldown`'s (event, routine) window — 12 h for
    `cost_trend_degraded` — is shared by every host row whichever routine read it. The second
    and third detections inside that window are dropped, and what the operator reads is one
    finding about the box.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    att = _attribute(degrading=3, steady=1)
    for slug in ("llmsectest-weekday", "library-sync", "folder-reorg"):
        routine, detail, fields = event_payload(slug, _trend_verdict(), att)
        log_health_event(home, "cost_trend_degraded", routine=routine,
                         run_id=f"{slug}:20260928-000003", detail=detail, **fields)
    rows = [json.loads(x) for x in
            (home / ".control" / "health-events.jsonl").read_text().splitlines()]
    assert len(rows) == 1, f"one host condition, {len(rows)} findings"
    assert rows[0]["routine"] == "" and rows[0]["scope"] == "host"
    assert rows[0]["detected_on"] == "llmsectest-weekday"


def test_two_routines_own_regressions_still_file_two_rows(tmp_path):
    """The collapse is of the HOST row only: two routines each with their own regression are
    two facts, and the per-routine cooldown is what governs them, exactly as before."""
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    att = _attribute(degrading=0, steady=4)
    for slug in ("alpha", "beta"):
        routine, detail, fields = event_payload(slug, _trend_verdict(), att)
        log_health_event(home, "cost_trend_degraded", routine=routine,
                         run_id=f"{slug}:r1", detail=detail, **fields)
    rows = [json.loads(x) for x in
            (home / ".control" / "health-events.jsonl").read_text().splitlines()]
    assert [r["routine"] for r in rows] == ["alpha", "beta"]
    assert {r["scope"] for r in rows} == {"routine"}


def _trend_verdict() -> dict:
    """What `run_health.recent_trend` hands the emitter for the subject above."""
    return {"evaluated": True, "flagged": True, "window": REGRESSION_WINDOW,
            "reasons": [(f"median volume ballooned {CHEAP:,.0f} → {DEAR:,.0f} "
                         "(≥2.7× and +2,000,000)")],
            "before": {"runs": 5, "fail_rate": 0.0, "turns_median": 10,
                       "volume_median": CHEAP},
            "after": {"runs": 5, "fail_rate": 0.0, "turns_median": 10,
                      "volume_median": DEAR}}
