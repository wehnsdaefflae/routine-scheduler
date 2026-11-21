"""What happens to a REPORT when the operator answers the question it raised.

A report carrying `options` is two things at once: work routed to its target, and a question
for him. Answering it settles the ASKING run's record — and until now that was all it did:

* the TARGET was told, by `relay` below, because an answer filed into someone else's inbox is
  invisible to the routine that must act on it (F621);
* but the REPORTS LEDGER was told nothing. A reader of `.control/reports.jsonl` — every triage
  pass, `gu rsched reports`, the Messages page — saw the filing and no answer.

For a ROUTED report that gap was survivable: the row has an owner, and the owner now knows.
For an UNROUTED one it is not. A report filed without a target waits in triage for a routine
to pick it up, and the one fact that decides what to do with it — that the operator has
already said what he wants — reached nobody at all, because `relay` returns early without a
target. Measured on 2026-11-21: R3630 was answered "Both" on 2026-11-20 at 11:58 and
`gu rsched reports --triage --status open` still listed it as unanswered work the next day,
whereupon the triaging run (self-audit) forwarded it to the util's owner offering three
options the operator had already chosen between. The answer existed for 23 hours in a place
no reader of reports looks.

So the answer is recorded as a ledger EVENT, and deliberately NOT as a terminal one
(`reportproj.TERMINAL_EVENTS`): a decided report is not a disposed-of report — unrouted work
still needs an owner, and routed work still needs building. The row stays on the open front
and now says what was decided.
"""

from __future__ import annotations

from pathlib import Path

from ..hooks import reportproj
from ..paths import append_jsonl, file_lock
from ..reports import reports_path

#: What the answer's text is cut to in the ledger row. The full answer lives in the question's
#: own record; this is the handle a triager reads, and a ledger row is not a transcript.
ANSWER_MAX = 400


def record(routines_home: Path, report_id: str, answer: str, *, routine: str = "") -> bool:
    """Append the `answered` event for `report_id`. True when a row was written.

    Best-effort, like every other write on the answer path: the operator's answer is already
    durably filed in the question's record, and a ledger that could not be appended must never
    fail his click.
    """
    text = " ".join(str(answer or "").split())[:ANSWER_MAX]
    if not report_id or not text:
        return False
    path = reports_path(routines_home)
    try:
        with file_lock(path.with_suffix(".lock")):
            append_jsonl(path, reportproj.event(routines_home, report_id=report_id,
                                                event="answered", by=text, to=routine))
    except OSError:
        return False
    return True


def relay(server, match: dict, text: str) -> None:
    """Tell the routine that OWNS a report-born decision's work what was decided (F621), and
    record the answer in the reports ledger whether or not there IS such a routine.

    The target gets an ordinary inbox message its next scheduled run drains, keyed on the
    report id — which is also what stops a second answer queueing a second message about one
    report. The ledger row is written for every answered report, routed or not, because the
    unrouted case is the one with no other reader (see the module note).
    """
    report_id = str(match.get("report") or "")
    if not report_id:
        return
    target = str(match.get("report_target") or "")
    record(server.routines_home, report_id, text, routine=target)
    if not target:
        return
    target_dir = server.routines_home / target
    if not (target_dir / "routine.yaml").is_file():
        return
    from ..engine import inbox

    inbox.file_message(target_dir,
                       f"[decision on {report_id}] The user answered the question that report "
                       f"raised, from {match.get('routine')}: {text}",
                       via="web", name=f"decision-{report_id}")
