"""The report ledger READ FROM THE HOOK STREAM — D152 campaign step 9, "replace last".

`hooks/store.py` states the design as *wrap first, replace last*: every mechanism mirrors into
the stream, then each one in turn stops reading its own projection. Step 4 did the write half
for reports — the hook is written first and `reports.jsonl` is derived from it. This module does
the READ half, and it is the step the campaign was named for: from here `reports.read_reports`
answers from `.control/hooks.jsonl`, and `reports.jsonl` is a projection nothing in this engine
reads to decide anything.

**Why this is one module and not twenty.** Roughly twenty modules answer a question from the
report ledger — the open-thread cap, the item read models, the orphan sweep, the Messages page,
the priorities fold, the finish hold. Not one of them is touched here, because every one of them
goes through `reports.read_reports`. The seam was already in the right place; what was behind it
was the wrong file.

**The property this rests on, and it is measured rather than asserted.** Folding the stream must
give the ledger, row for row and key for key. Measured on the live instance the day this
shipped: 5,334 hooks against 5,334 ledger rows, folding to 2,868 reports on both sides, equal as
LISTS — same rows, same order, same key order inside each row. That took one repair first: 8
rows filed on 2026-10-19, in the window between the mirror (step 2) and the source (step 4),
whose hooks never carried their disposal (`migrate_mirror_hooks` — without it a read served from
the stream would have silently re-opened six settled exchanges).

**What the ledger file is FOR, now that nothing here reads it.** Three things, all of them
outside this engine: the operator reads it by eye, `self-audit`'s own scripts fold it, and it is
the fallback this module falls back TO. That is why step 9 does not remove the write — the
campaign's last step does, and only once nothing outside depends on it.

**ONE RULE, and it is chosen so that no reading of either store can lose a report:** the stream
decides, and whenever the ledger holds a report the stream's fold does not, the LEDGER's fold is
served instead and the disagreement is filed. Losing a report is the one failure a read may not
have — a dropped row silently un-owes a reply, empties a backlog count and takes a finding off
the operator's page, and none of those announce themselves. A stream with no report rows at all
is the same rule's limit case, reported under its own name (`hook_stream_unreadable`) because
"the store is gone" and "the store is behind" want different repairs.

That rule is also what makes the step cheap to adopt rather than a fleet-wide rewrite: a
`reports.jsonl` written by hand — the operator's own edit, a test fixture, a restore from backup
— keeps being served exactly as before, while every row that went through the one writer is
served from the stream. What the step changes is WHICH store the engine believes by default, and
the campaign's last step (removing the mirror write) is what makes the comparison moot.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..health_events import log_health_event
from .reportproj import stream_rows
from .store import hooks_path

log = logging.getLogger("rsched.hooks")


def sources(routines_home: Path) -> list[Path]:
    """Both stores, as the inputs a memo fingerprint must cover.

    The stream is what is READ; the ledger is still what every out-of-engine reader folds and
    what this module falls back to, so a cache vouched for by the stream alone would serve a
    stale answer after a hand-edit of the ledger. Two paths, one fingerprint — the memo
    invalidates when either changes (`readmodels/memo.fingerprint`).
    """
    from ..reports import reports_path

    return [hooks_path(routines_home), reports_path(routines_home)]


def read_reports(path: Path) -> list[dict]:
    """Every report, folded into one row per id, in filing order — THE read, for every caller.

    Re-exported as `reports.read_reports`, which is the name its ~20 callers hold; it lives
    here because this module owns what is read. `path` still names the LEDGER: it identifies
    the instance, every caller already holds it, and the stream is found beside it.

    A `delivered`, `retracted` or `superseded` event is merged into its report as a
    `delivered: {ts, run_id}` / `retracted: {ts}` / `superseded: {ts, by, to}` key; an event
    naming no report is dropped rather than becoming a phantom item.

    Folded ONCE per append, behind the read-models' stat fingerprint over BOTH stores: the
    stores are append-only and together ~6 MB on the live instance, and the fold was re-run by
    every routine-page open (the Messages panel), every orphans read and every outbox
    retraction — a per-request cost that grows with how much the fleet talks. The returned rows
    are SHARED and must be treated as immutable; every caller folds them into new dicts.
    """
    from ..readmodels import memo

    home = path.parent.parent
    return memo.memoized_shared(f"reports:{path}", sources(home), lambda: folded(home))


def projection(routines_home: Path) -> list[dict]:
    """`reports.jsonl` folded — the PROJECTION's own answer, read from the file.

    One function for all three readers of it (this module's shortfall check and the two
    comparators in `hooks/reportinv`), because "what does the old store say" must be one
    reading: two spellings of it is how step 7's spurious divergences happened. Module-level
    rather than a function-local import, so a test can substitute it — a `reports.jsonl` that
    cannot be read is a state no fixture can otherwise produce, since `paths.read_jsonl`
    answers a missing or corrupt file with an empty list.
    """
    from ..paths import read_jsonl
    from ..reports import fold_rows, reports_path

    return fold_rows(read_jsonl(reports_path(routines_home)))


def folded(routines_home: Path) -> list[dict]:
    """Every report, folded, read from the HOOK STREAM — the ledger's own rows, derived.

    The fold is `reports.fold_rows`, never a second implementation of it: one stream read two
    ways is how two readings come to disagree (the whole lesson of step 7's spurious
    divergences). `stream_rows` is the raw projection — a filing and each lifecycle fact as its
    own row — which is exactly what that fold takes.

    Then the one rule that cannot lose a report: if the LEDGER names a report the stream's fold
    does not, the ledger's fold is served and the shortfall is filed. Both the all-or-nothing
    cases fall out of it — a stream with no report rows, and a stream that is simply behind —
    and they are reported apart, because "the store is gone" and "the store is behind" want
    different repairs. Note what this does NOT catch, and why the repair migration is not
    optional: a row the stream has but POORER (the mirror era's missing disposal) carries the
    same id, so no id comparison can see it. That is checked once, at boot, where it can be
    corrected rather than worked around.
    """
    from ..reports import fold_rows

    try:
        rows = stream_rows(routines_home)
    except OSError:
        log.exception("report-hooks: reading the hook stream for the ledger read raised")
        rows = []
    stream = fold_rows(rows)
    old = projection(routines_home)
    have = {str(s.get("id") or "") for s in stream}
    missing = [i for r in old if (i := str(r.get("id") or "")) and i not in have]
    if not missing:
        return stream
    _fell_short(routines_home, missing, served=len(old), from_stream=len(stream))
    return old


def _fell_short(routines_home: Path, missing: list[str], *, served: int,
                from_stream: int) -> None:
    """File the event that says the read did NOT come from the stream — the one place for it.

    Two names for one shortfall, because the repair differs: `hook_stream_unreadable` when the
    stream carries no report rows at all (it was lost, or this instance is fresh), and
    `hook_stream_diverged` when it carries some but not all (a row reached `reports.jsonl`
    without going through the one writer, or the backfill is incomplete).

    Best-effort: a diagnostic may not be the reason a page fails to render. But it is filed at
    all because the alternative is the projection serving the whole console while the store that
    replaced it is empty, and nothing anywhere saying so.
    """
    gone = from_stream == 0
    detail = (f"the report read served reports.jsonl ({served} reports) instead of the hook "
              f"stream, which "
              + ("holds no report rows at all" if gone
                 else f"folded to {from_stream} and is missing {len(missing)}: "
                      f"{', '.join(missing[:12])}")
              + ". Since D152 step 9 the stream is what every report surface reads and the "
                "ledger is its projection, so this is a shortfall in the stream, not in the "
                "read. Either the stream was lost (restore it, or rebuild it from the ledger "
                "through migrate_report_hooks), or a row reached the ledger without going "
                "through the one writer (hooks/reportproj)")
    try:
        log_health_event(
            routines_home, "hook_stream_unreadable" if gone else "hook_stream_diverged",
            routine="", run_id="", detail=detail, rows=served, stream=from_stream,
            missing=len(missing))
    except Exception:                        # noqa: BLE001 — a diagnostic may never raise here
        log.warning("report-hooks: the shortfall event could not be filed")
    log.warning("report-hooks: %s", detail)
