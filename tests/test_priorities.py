"""Where a ⚑ priority flag LANDS: the owner resolution in `rsched/priorities.py` (D75, D240).

A flag reaches the digest of the routine that owns the flagged row. Since self-audit was split
into maintenance (`self-audit`) and feature development (`scheduler-builder`), a finding or
decision self-audit has HANDED OVER is the builder's work, so the flag has to reach the
BUILDER's digest — the routine that may act on it — and not the ledger keeper's (D240). The
store round-trip, the `R<n>` side of the resolver and the Messages-page sort live in
`test_items.py`; this module is the F/D owner rule and the one marker both sides read.
"""

from __future__ import annotations

import json

import pytest

from rsched import priorities
from rsched.readmodels import item_counts, memo
from rsched.readmodels import items as items_model

#: The TAIL of a real handed-over row, verbatim from the live ledger
#: (`<routines>/self-audit/audit/report.json`, F-row read 2026-11-09; the measurement prose
#: ahead of it is elided at `[…]`). Two properties of the real text the predicate must survive:
#: the marker sits MID-PARAGRAPH, after the finding's own prose, and a later run APPENDS
#: paragraphs behind it — so a predicate anchored at the start of `detail`, or one that reads
#: only its first line, matches none of the 123 rows that carried the marker that day.
HANDED_DETAIL = (
    "[…] WHAT DONE LOOKS LIKE: each of the five is either deleted with its migration (the "
    "converged ones) or its marker extended with a reason, before 2026-11-15. Handed to "
    "scheduler-builder 2026-11-06: retire or extend the five MIGRATION(expires=2026-11-15) "
    "markers listed above before that date, deleting the converged migrations outright; a gate "
    "red on the 15th is a self-inflicted stop for both our routines.\n\n"
    "Shipped by scheduler-builder in 0.505.0 (dbda15e4) — scheduler-builder:20261106-140001."
)

#: A row self-audit kept: it names the builder nowhere, whatever its prose is about.
KEPT_DETAIL = "MEASURED 2026-11-08: the ledger's own drift, and scheduler-builder is not in it."


def _report() -> dict:
    """A ledger holding the four shapes the rule has to separate: handed and in progress,
    handed but already addressed, never handed, and handed prose on an `open` row.
    """
    return {
        "schema": 1, "run_id": "self-audit:20261108-000001",
        "generated": "2026-11-08T01:00:00+02:00",
        "findings": [
            {"id": "F854", "severity": "problem", "status": "in_progress",
             "title": "the deploy checks never run", "detail": HANDED_DETAIL,
             "owner": "scheduler-builder"},
            {"id": "F900", "severity": "problem", "status": "open",
             "title": "the ledger keeper's own drift", "detail": KEPT_DETAIL,
             "owner": "self-audit"},
            {"id": "F901", "severity": "info", "status": "open",
             "title": "written up, not handed yet", "detail": HANDED_DETAIL,
             "owner": "self-audit"},
        ],
        "decisions": [
            {"id": "D277", "title": "a decision being built", "status": "in_progress",
             "detail": HANDED_DETAIL, "options": [], "owner": "scheduler-builder"},
            {"id": "D239", "title": "built already, row still here", "status": "addressed",
             "detail": HANDED_DETAIL, "options": [], "owner": "scheduler-builder"},
        ],
    }


@pytest.fixture
def home(tmp_path):
    """A routines home whose self-audit ledger holds `_report()`."""
    memo.reset()
    routines = tmp_path / "routines"
    audit = routines / "self-audit" / "audit"
    audit.mkdir(parents=True)
    (audit / "report.json").write_text(json.dumps(_report()), encoding="utf-8")
    return routines


def test_a_flagged_handed_over_row_reaches_the_builder_not_the_keeper(home):
    """The operator's ⚑ on work being built lands in the digest of the routine building it."""
    priorities.set_priority(home, "D277", True)
    builder = priorities.digest_section(home, priorities.BUILDER_SLUG)
    assert "D277" in builder and "a decision being built" in builder
    assert priorities.digest_section(home, priorities.SELF_AUDIT_SLUG) == ""


def test_a_flagged_un_handed_row_stays_with_the_ledger_keeper(home):
    priorities.set_priority(home, "F900", True)
    assert "F900" in priorities.digest_section(home, priorities.SELF_AUDIT_SLUG)
    assert priorities.digest_section(home, priorities.BUILDER_SLUG) == ""


def test_an_addressed_handed_row_returns_to_the_ledger_keeper(home):
    """A flag on FINISHED work reaches someone who can clear or answer it, not the builder."""
    priorities.set_priority(home, "D239", True)
    assert "D239" in priorities.digest_section(home, priorities.SELF_AUDIT_SLUG)
    assert priorities.digest_section(home, priorities.BUILDER_SLUG) == ""


def test_the_hand_over_needs_both_the_marker_and_the_status(home):
    """`in_progress` alone is not a hand-over, and the marker alone is not either: F901 carries
    the prose on an `open` row, which is self-audit's until it hands the row over.
    """
    priorities.set_priority(home, "F901", True)
    assert "F901" in priorities.digest_section(home, priorities.SELF_AUDIT_SLUG)
    assert priorities.digest_section(home, priorities.BUILDER_SLUG) == ""


def test_a_flagged_row_the_ledger_no_longer_holds_stays_with_the_keeper(home):
    """No row, no hand-over to read: the flag reaches the routine that owns the ledger, which is
    who can restore the row or clear the flag.
    """
    priorities.set_priority(home, "F7777", True)
    assert "F7777" in priorities.digest_section(home, priorities.SELF_AUDIT_SLUG)
    assert priorities.digest_section(home, priorities.BUILDER_SLUG) == ""


def test_one_constant_owns_the_marker_self_audit_writes():
    """The recipes' wording and this resolver cannot drift apart silently: the predicate is the
    `HANDED_TO_BUILDER` constant, pinned here against the real `detail` text above. The line is
    written by self-audit's `write-report` stage (its recipe, outside this repo, states
    `Handed to scheduler-builder <date>: <what to build>` with status `in_progress`).
    """
    assert priorities.HANDED_TO_BUILDER == "Handed to scheduler-builder"
    assert priorities.BUILDER_SLUG == "scheduler-builder"
    assert priorities.HANDED_TO_BUILDER in HANDED_DETAIL
    assert priorities.HANDED_TO_BUILDER not in KEPT_DETAIL
    handed = {"status": "in_progress", "detail": HANDED_DETAIL}
    assert priorities.flagged_item_owner(handed) == priorities.BUILDER_SLUG
    assert priorities.flagged_item_owner({"status": "in_progress", "detail": KEPT_DETAIL}) \
        == priorities.SELF_AUDIT_SLUG
    assert priorities.flagged_item_owner({}) == priorities.SELF_AUDIT_SLUG


def test_the_messages_page_names_the_same_owner_as_the_digest(home):
    """The read model and this resolver must agree on a row being built: the page's `owner`
    column (`item_counts.owner_of`, verbatim from the row) and the digest's flag owner both say
    the builder, so the operator's ⚑ and the card he set it on do not name different routines.
    """
    priorities.set_priority(home, "F854", True)
    card = next(i for i in items_model.build(home / "self-audit", home)["items"]
                if i["id"] == "F854")
    assert card["priority"] is True
    assert item_counts.owner_of(card) == priorities.BUILDER_SLUG
    assert [i["id"] for i in priorities.owned_priority_items(home, priorities.BUILDER_SLUG)] \
        == ["F854"]
