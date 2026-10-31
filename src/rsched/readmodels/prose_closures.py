"""A live row some SETTLED report's prose claimed to dispose of — the third way work is lost
from the report ledger (F793).

`readmodels/orphans.py` carries the other two, and this is the same shape of loss: a row that is
open, counted in every backlog figure, and that nobody is looking for. What is different is WHY
nobody is looking — and it is the worst of the three reasons, because the row looks attended to.
Some report said in its own words that it disposed of it.

## The gap this closes

F770 shipped the predicate (`report_threads.undisposed_claims`) and gave it exactly ONE reader:
the filing observation in `engine/admin_handlers.py`, which WARNS and deliberately does not
refuse — the prose may be a citation the check reads as a claim. So the claim is evaluated once,
at filing time, in front of the one party who already believes the row is closed. A run that does
not act on its own observation strands the row exactly as before, and nothing evaluates it again,
ever. R2886 is the specimen and also the proof that the detector could not reach its own
motivating row: R2886 was filed 2026-10-24 ("Closing R2313. Both halves disposed of", with
`answers: "R2743"`, `closes: true`, no `settles`), the detector shipped 2026-10-25, and R2313 was
still the oldest live row on the instance 20 days later.

The asymmetry is what makes it durable (F770's own docstring says it): the sender reads its own
prose and is satisfied, while the recipient sees a live row and may not close it — a row is
disposed of only for the routine it was delivered TO, which is correct. So this flag names that
OWNER, because the owner is the only one whose `settles` can end it.

## Why only a SETTLED claimant counts

While the claiming report is itself open, the pair is being worked: the row is on somebody's
worklist, the thread is alive, and the filing warning is still in front of the author who can add
the field. The stall becomes PERMANENT at the moment the claimant reads as settled — nothing will
re-open that observation, and the claim is now a statement in a finished thread. So the narrowing
is not frugality, it is the finding: this surface flags what the filing warning can no longer
reach. A retracted claimant is excluded for the reason `orphans.find_undelivered` excludes one —
retraction unlinks a report on purpose, so a withdrawn claim claims nothing.

## One predicate, not a second opinion

Both halves are borrowed rather than re-derived (the F768 discipline — two surfaces disagreeing
about whether work is live is the defect this family of checks exists to end):

- WHAT COUNTS AS A CLAIM is `report_threads.undisposed_claims`, over the same two texts
  (`title`, and the detail's `opening_sentence`) and against the same three disposal fields the
  filing check compares — so this surface and that warning can never read one report differently,
  and `tests/test_report_claims.py` remains the one statement of the rule.
- WHETHER A ROW IS LIVE is `report_threads.still_owed`, the ledger's own reading of open — so a
  row some OTHER report settled is not flagged here while the backlog counts it settled.

## What it costs to read

`reports.read_reports` — the whole folded ledger (2.9 MB / ~2,200 rows on the live instance),
which this surface does not re-parse: the fold is memoized behind the read-models' stat
fingerprint and shared with every other reader of the stream (the Messages panel, the two orphan
banners, every outbox retraction), so a third reader adds no parse. On top of it this module runs
two short regexes over each row's title and first sentence, memoized behind that same
fingerprint, because `GET /api/items/orphans` is polled by an open console. The ROW keeps its
detail whole (D177), so the prose a settled report claimed with is still on disk to be judged.
"""

from __future__ import annotations

from pathlib import Path

from .. import report_threads
from . import memo

#: What the flag is called on the wire, beside `kind: "deferral"` / `kind: "undelivered"`.
KIND = "prose-closure"


def _claimed_by(row: dict) -> dict[str, str]:
    """Every id this row's prose claims to dispose of, mapped to the exact words that claimed
    it — `{}` for the overwhelming majority of reports, which claim nothing.

    The quote is the text that was JUDGED (the title, or the detail's opening sentence), so the
    reader never sees prose the check did not read and never misses the prose it did. Each text
    is put through `undisposed_claims` on its own, as its `title`, which is how the one rule
    answers "which text claimed this" without a second implementation of the clipping.
    """
    disposed = (str(row.get("answers") or ""),
                *(row.get("settles") or ()), *(row.get("supersedes") or ()))
    texts = (str(row.get("title") or ""),
             report_threads.opening_sentence(str(row.get("detail") or "")))
    out: dict[str, str] = {}
    for text in texts:
        for rid in report_threads.undisposed_claims(title=text, detail="", disposed=disposed):
            out.setdefault(rid, text.strip())
    return out


def find(rows: list[dict]) -> list[dict]:
    """Every live row a settled report's prose claimed to dispose of, NEWEST CLAIM FIRST.

    `rows` is the folded stream (`reports.read_reports`). The newest claim leads because that is
    the one still fixable by the run that made it — the older a prose-only closure is, the more
    certainly its author is gone.
    """
    claims: list[tuple[dict, str, str]] = []
    for row in rows:
        if row.get("retracted"):
            continue                 # withdrawn on purpose: it claims nothing
        for rid, words in _claimed_by(row).items():
            claims.append((row, rid, words))
    if not claims:
        return []

    # ONE liveness question for every id involved — the claimants' and the claimed rows' — so
    # the scan stays linear in the ledger rather than re-folding it per claim.
    claimants = [str(row.get("id") or "").strip().upper() for row, _, _ in claims]
    owed = set(report_threads.still_owed(rows, [*claimants, *(rid for _, rid, _ in claims)]))

    out: list[dict] = []
    by_id = {str(r.get("id") or "").strip().upper(): r for r in rows}
    for row, rid, words in claims:
        claimant = str(row.get("id") or "").strip().upper()
        if claimant in owed:
            continue                 # the claiming thread is still open — still being worked
        if rid not in owed:
            continue                 # the row really is disposed of, by this report or another
        live = by_id.get(rid, {})
        out.append({
            "kind": KIND,
            "id": rid,
            "title": str(live.get("title") or ""),
            "ts": str(live.get("ts") or ""),
            # the only routine whose `settles` can end this row (`answers`/`settles` dispose of
            # a row for the routine it was delivered TO — the engine refuses anyone else)
            "owner": report_threads.owner_of(rows, rid) or "",
            "claimed_by": claimant,
            "claimed_by_routine": str(row.get("routine") or ""),
            "claimed_ts": str(row.get("ts") or ""),
            # the claiming words themselves, so the reader judges the actual claim rather than
            # trusting this module's opinion of it (the same courtesy orphans pays a deferral)
            "claim": words,
        })
    out.sort(key=lambda o: o["claimed_ts"], reverse=True)
    return out


def load(routines_home: Path) -> list[dict]:
    """`find` over the live report stream, memoized behind the stream's own stat fingerprint.

    Shared (never deep-copied) like the fold underneath it: the rows are small and every caller
    concatenates them into a new list. Treat the result as immutable.
    """
    from ..reports import read_reports, reports_path

    path = reports_path(routines_home)
    return memo.memoized_shared(f"prose-closures:{path}", [path],
                                lambda: find(read_reports(path)))
