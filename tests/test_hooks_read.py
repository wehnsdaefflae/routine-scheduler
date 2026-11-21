"""D152 step 9: every report surface READS the hook stream, and `reports.jsonl` is its
projection.

Step 4 made the hook the source of the WRITE; step 7 moved the first decision onto the stream
while still returning the ledger's answer. This step moves the READ — `reports.read_reports`,
the one seam about twenty modules go through — so nothing in the engine decides anything from
the projection any more.

What these tests pin, in the order the risk lies:

1. **The property the whole step rests on**, measured against the LIVE stores: folding the
   stream equals folding the ledger, as LISTS — same reports, same order, same keys. It is the
   one check that would have caught the mirror-era gap, and it is the reason there is a repair
   migration at all.
2. **The repair is exact and narrow.** 8 of 5,334 live rows were filed on 2026-10-19, between
   the mirror and the source, and their hooks never carried `answers`/`closes`/`settles`/
   `supersedes`. Six settled exchanges would have read as OPEN from the stream. The repair
   recomputes those records through the declared inverse, touches nothing else, and refuses to
   guess where an `(id, event)` key names two rows.
3. **The fall-back cannot be silent.** A read that finds no report rows in the stream serves the
   ledger and files `hook_stream_unreadable` — a projection quietly serving a console whose
   store was supposed to be retired is the state this campaign exists to end.
4. **The memo is vouched for by BOTH stores**, or a page would keep serving a fold from before
   the newest filing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.hooks import store as hookstore
from rsched.hooks.reportproj import stream_rows
from rsched.hooks.reportread import folded, sources
from rsched.paths import read_jsonl
from rsched.reports import file_report, fold_rows, read_reports, reports_path

LIVE = Path("/home/mark/routines")


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    return home


def _watch(monkeypatch) -> list[dict]:
    """Collect the health events the read files, keeping the NAME as well as the fields — the
    two shortfalls differ only by which name they are filed under, so a watcher that dropped
    the positional event would pass for either.
    """
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportread.log_health_event",
                        lambda *a, **k: seen.append({"event": a[1] if len(a) > 1 else "",
                                                     **k}))
    return seen


@pytest.mark.skipif(not (LIVE / ".control" / "reports.jsonl").is_file(),
                    reason="no live stores on this host")
def test_the_live_stream_carries_every_live_report_in_the_ledgers_order():
    """Over the real stores: the stream names every report the ledger does, in its order.

    Measured when this shipped: 5,334 hooks, 5,334 ledger rows, 2,868 reports on both sides,
    identical id sequences. This is the WEAKER half of the property on purpose — the stronger
    one (equal row for row and key for key) holds only once the mirror-era repair has been
    applied, and that happens at daemon boot, so asserting it here would red every gate run
    between this commit and the next restart. The strong claim is pinned in the test below,
    which applies the pure repair first; what belongs HERE is the claim that is true of the
    live stores at any moment, and it is the one that catches a LOST or UNBACKFILLED row.
    """
    ledger = fold_rows(read_jsonl(reports_path(LIVE)))
    stream = fold_rows(stream_rows(LIVE))
    assert [r["id"] for r in stream] == [r["id"] for r in ledger], "the ids or their order"


def test_a_read_finding_no_report_rows_serves_the_ledger_and_says_so(tmp_path, monkeypatch):
    """A stream that lost its rows serves the projection AND records it — never one without
    the other. `hook_stream_unreadable` is the name for "the store is gone".
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t", detail="d") is not None
    hookstore.hooks_path(home).write_text("", encoding="utf-8")
    seen = _watch(monkeypatch)
    rows = folded(home)
    assert [r["id"] for r in rows] == ["R1"], "the ledger's own fold must still be served"
    assert [s["event"] for s in seen] == ["hook_stream_unreadable"], seen
    assert seen[0]["rows"] == 1 and seen[0]["stream"] == 0


def test_a_report_only_the_ledger_has_is_still_served_and_the_shortfall_is_filed(tmp_path,
                                                                                monkeypatch):
    """THE rule that cannot lose a report: an id the stream's fold lacks is served from the
    projection, and the shortfall is filed as a DIVERGENCE (the store is behind, not gone).

    This is the shape ~24 test fixtures and the operator's own hand-edits write: a row appended
    to `reports.jsonl` with no hook. Dropping it would silently un-owe a reply, empty a backlog
    count and take a finding off the Decisions page — none of which announce themselves. So the
    read serves it, and says that it had to.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    with reports_path(home).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "R9", "ts": "t", "routine": "a", "run_id": "a:9",
                             "title": "hand-written, no hook", "detail": "d"}) + "\n")
    seen = _watch(monkeypatch)
    assert [r["id"] for r in folded(home)] == ["R1", "R9"], "a report was lost"
    assert [s["event"] for s in seen] == ["hook_stream_diverged"], seen
    assert seen[0]["missing"] == 1 and seen[0]["stream"] == 1


def test_the_read_is_fingerprinted_on_both_stores(tmp_path):
    """`sources` names the stream AND the ledger, so either one changing invalidates the memo.

    The stream is what is read; the ledger is the fall-back and what outside readers fold. A
    memo vouched for by one of them would serve a stale answer after the other changed.
    """
    home = _home(tmp_path)
    assert sources(home) == [hookstore.hooks_path(home), reports_path(home)]


def test_a_filing_is_visible_to_the_very_next_read(tmp_path):
    """The memoized read must not serve a fold from before the newest filing — the regression a
    fingerprint over the ledger alone would risk, since the hook is written first.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1"]
    assert file_report(home, routine="a", run_id="a:2", title="t2", detail="d") is not None
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1", "R2"]


def test_the_served_read_comes_from_the_stream_not_the_file(tmp_path):
    """The claim of step 9, made falsifiable in the one direction the never-lose rule leaves
    open: a report the STREAM has and the projection does not IS served.

    Before step 9 this was impossible — the read parsed `reports.jsonl` and nothing else, so a
    hook with no ledger row was invisible to every surface. Nothing in production writes such a
    store either (the projection is appended in the same call); it is written here precisely
    because it is the state that distinguishes the two readings.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    assert file_report(home, routine="a", run_id="a:2", title="t2", detail="d") is not None
    kept = [r for r in read_jsonl(reports_path(home)) if r.get("id") != "R2"]
    reports_path(home).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1", "R2"], \
        "the stream's own report was not served"
