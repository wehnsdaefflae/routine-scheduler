"""Thread concentration on the report ledger: how many threads one sender has open to one
owner, which rows a new report may fold into itself, and which a run still owes.

Split out of `reports.py` because it is a different responsibility — that module WRITES the
ledger, this one READS it to answer two questions the write path asks first:

- **How many open threads does this sender already have to this owner?** The open-thread cap
  (D110, operator 2026-08-31) refuses the one that would make it worse, and names the ids so
  the run has something to fold into. A refusal that only counts is a refusal that loses the
  finding.
- **Which of these ids can this report take over?** `supersedes` is the operation the
  `problem-routing` rule has asked for since 2026-08-31 ("add your evidence to the OLDEST open
  one rather than opening another") and that nothing implemented, which is why the rule did not
  hold: the ledger is append-only and there was no way to add to a thread.

— plus the one the pre-finish routing assist asks as a run ends (`still_owed`). All three read
"open" the same way (`_is_open`), and all take the FOLDED rows (`reports.read_reports`) rather
than a path — they are pure questions about a stream the caller already holds (under the
ledger lock, on the write path), and a second read inside the lock would be a second answer to
the same question.
"""

from __future__ import annotations

import re
from collections.abc import Collection

#: A DISPOSITION VERB standing beside a report id: the one shape that distinguishes "closing
#: R2313" from "R2313 is why I am writing" (F770). Narrow on purpose — the check says something
#: the author can act on in the turn he still can, and a predicate that fired on every report
#: discussing the ledger would be ignored within a week. The verb may lead ("Closing R2313",
#: "Settled: R900") or follow ("R2313 is closed by this"), with a few words of slack either way
#: but never across a sentence boundary.
_VERB = re.compile(r"\b(?:clos(?:e|es|ed|ing)|settl(?:e|es|ed|ing)|dispos(?:e|es|ed|ing))\b",
                   re.IGNORECASE)
#: The slack between the verb and the id it governs — enough for "Disposing of R12 and R13",
#: "Closing, as agreed, R44" or "R2313 is closed by this", and short enough that the verb and
#: the id have to belong to one clause. A sentence boundary ends the window whatever its width.
_SLACK = 40
_ID = re.compile(r"\bR\d+\b")


def _opening_sentence(text: str) -> str:
    """The first sentence of `text` — where a report states what it is doing.

    The cheap version this check wants (F770): a report's BODY argues about rows for paragraphs
    ("F324 closed without naming it", "R2313 was closed in error"), so reading all of it would
    flag the evidence of every report that discusses the ledger at all.
    """
    return re.split(r"(?<=[.!?])\s|\n", (text or "").strip(), maxsplit=1)[0]


def undisposed_claims(*, title: str, detail: str, disposed: Collection[str]) -> list[str]:
    """Report ids this report's OPENING WORDS claim to have closed, while no field disposes of
    them — in the order they are claimed.

    `disposed` is every id the same report really disposes of (`answers`, `settles`,
    `supersedes`), compared case-insensitively.

    R2886 is the specimen: it opened "Closing R2313. Both halves disposed of" and argued it for
    paragraphs, while its fields carried `answers: "R2743"` and `closes: true` and no `settles`.
    So it closed its own chase and left R2313 open — from then on the oldest live row on the
    instance, reported by every backlog count for a row both its sender and its reader believed
    was disposed of. The asymmetry is what makes it durable: the sender reads its own prose and
    is satisfied, while the recipient sees a live row and cannot close it (a row is disposed of
    only for the routine it was delivered TO, which is correct). Nothing compared the TEXT
    against the fields, so the gap between "said" and "did" was invisible from both sides at
    once — the report-ledger twin of a decision answered with no hand-over line.
    """
    done = {str(i).strip().upper() for i in (disposed or ())}
    out: list[str] = []
    for text in (title, _opening_sentence(detail)):
        # Per SENTENCE, because a verb governs only its own clause: every id within `_SLACK` of
        # a disposition verb is claimed, so "Disposing of R12 and R13" names both rather than
        # only the first, while "Closing R44. R2313 is the evidence" names only R44.
        for sentence in re.split(r"[.!?\n]", text or ""):
            for verb in _VERB.finditer(sentence):
                for match in _ID.finditer(sentence):
                    if not (verb.start() - _SLACK <= match.start()
                            and match.end() <= verb.end() + _SLACK):
                        continue
                    claimed = match.group(0).upper()
                    if claimed not in done and claimed not in out:
                        out.append(claimed)
    return out


#: How many reports one routine may keep OPEN to one owner before the next is refused (D110,
#: operator 2026-08-31: "at 3+ open to one target, reply to the oldest instead of filing new").
#: Low on purpose: the rule is about the reflex to open a parallel thread, not about a volume
#: anyone would defend. A reply (`answers`) and a consolidation (`supersedes`) are never capped
#: — both REDUCE the thread count, and capping them would punish the way out.
OPEN_THREAD_CAP = 3


class ThreadCapError(Exception):
    """`routine` already has `open_ids` open to `target` — this would be one thread too many.

    Carries the ids because the observation's whole job is to put them in front of the run:
    a refusal that only says "too many" leaves it with nothing to fold into.
    """

    def __init__(self, routine: str, target: str, open_ids: list[str]) -> None:
        super().__init__(f"{routine} already has {len(open_ids)} reports open to {target}: "
                         f"{', '.join(open_ids)}")
        self.routine, self.target, self.open_ids = routine, target, open_ids


def _disposed(rows: list[dict]) -> set[str]:
    """Every id some report has DISPOSED OF. Two faces, both counting (D134): `answers` ends
    one exchange, `settles` ends every row it names. A RETRACTED reply disposes of nothing —
    its answer never arrived.
    """
    return {str(i).strip().upper()
            for r in rows if not r.get("retracted")
            for i in (str(r.get("answers") or ""), *(r.get("settles") or []))} - {""}


def _is_open(row: dict, disposed: set[str]) -> bool:
    """Does the owner still have this row? Not retracted, not folded into another thread,
    not itself a closure, and not disposed of by any report — the same reading
    `readmodels/items.py` derives a status from, computed here from the rows directly,
    because a WRITE precondition must never depend on a read model.
    """
    return not (row.get("retracted") or row.get("superseded") or row.get("closes")
                or str(row.get("id") or "").strip().upper() in disposed)


def open_threads(rows: list[dict], *, routine: str, target: str) -> list[str]:
    """The report ids `routine` still has open to `target`, OLDEST FIRST (the ledger's own
    order), so the caller can name the one to fold into.

    Counting only `answers` as disposal would keep a settled row in the cap's tally, so the
    cap would go on refusing new reports about work the ledger already knows is finished —
    which is why `_disposed` reads `settles` too.
    """
    disposed = _disposed(rows)
    return [str(r.get("id"))
            for r in rows
            if r.get("routine") == routine and r.get("target") == target
            and _is_open(r, disposed)]


def owner_of(rows: list[dict], rid: str) -> str | None:
    """Whose row is this — the routine it was DELIVERED to — or `""` when nobody owns it yet.
    `None` means the ledger does not have the id at all.

    A FOLDED row's owner is its carrier's target, following the chain to its end: `supersedes`
    makes the carrier the row's thread, so the thread's owner is who holds it (the same reading
    `readmodels/item_reports.report_row_item` renders as "folded into R<n> → <owner>"). Without
    that, handing rows to their owner and then answering as that owner — the ordinary
    consolidate-then-reply pass — would refuse itself.
    """
    by_id = {str(r.get("id") or "").strip().upper(): r for r in rows}
    seen: set[str] = set()
    at = str(rid).strip().upper()
    while at and at in by_id and at not in seen:
        seen.add(at)
        row = by_id[at]
        if target := str(row.get("target") or "").strip():
            return target
        fold = row.get("superseded")
        fold = fold if isinstance(fold, dict) else {}
        nxt = str(fold.get("by") or "").strip().upper()
        if not nxt:
            return str(fold.get("to") or "").strip()
        at = nxt
    return None if not seen else ""


def disposable(rows: list[dict], ids: list[str], *,
               replier: str) -> tuple[list[str], list[str], list[str]]:
    """Split `ids` into those `replier` may dispose of, those delivered to SOMEONE ELSE, and
    those nobody has been given yet — the caller's order, each id named once (D182).

    `answers` and `settles` dispose of a row *for the routine the row was delivered to*: the
    engine's own prose says `answers` is "the id of a report you RECEIVED". Nothing enforced
    that, so a row left the ledger whenever ANY report named it — including its own sender's.
    Measured over the 2,110-row ledger before the fix: 492 rows were disposed of by someone
    who was not their owner, 317 of them never routed to anyone, so they left triage with no
    owner having read them. The operator's own bug report R2552 vanished that way 14 seconds
    after he filed it.

    Two refusals, kept apart because they need different moves: a row delivered elsewhere is
    its owner's to answer (reply to the owner, or `supersedes` it to take the thread over),
    while an UNROUTED row has to be routed before it can be settled at all. An id the ledger
    does not have is `unrouted` too — there is nothing to own.

    This is a WRITE precondition only. The read model is deliberately untouched, so the rows
    already disposed of this way stay settled: re-deriving them would hand triage back
    hundreds of rows at once, which is not what fixing the gate means.
    """
    allowed: list[str] = []
    elsewhere: list[str] = []
    unrouted: list[str] = []
    for raw in ids:
        rid = str(raw).strip().upper()
        if not rid or rid in allowed or rid in elsewhere or rid in unrouted:
            continue
        owner = owner_of(rows, rid)
        if owner and owner == replier:
            allowed.append(rid)
        elif owner:
            elsewhere.append(rid)
        else:
            unrouted.append(rid)
    return allowed, elsewhere, unrouted


def supersedable(rows: list[dict], ids: list[str]) -> tuple[list[str], list[str]]:
    """Split `ids` into those a new report may take over and those it may not, preserving the
    caller's order and naming each id once. Unusable means unknown to the ledger, already
    retracted, or already folded into some other thread — a row cannot be in two threads at
    once.
    """
    by_id = {str(r.get("id") or "").upper(): r for r in rows}
    ok: list[str] = []
    bad: list[str] = []
    for raw in ids:
        rid = str(raw).strip().upper()
        if rid in ok or rid in bad:
            continue
        row = by_id.get(rid)
        (bad if row is None or row.get("retracted") or row.get("superseded") else ok).append(rid)
    return ok, bad


def still_owed(rows: list[dict], ids: list[str]) -> list[str]:
    """Which of `ids` nobody has disposed of yet, in the caller's order (`_is_open`).

    The pre-finish routing assist asks this at the moment a run ends (R2086). The run's own
    list of what it owes only learns of the replies it files itself, so a thread closed any
    other way — by another routine, by the operator, through a fold — would otherwise keep a
    finished run answering for work that is already done.
    """
    disposed = _disposed(rows)
    by_id = {str(r.get("id") or "").upper(): r for r in rows}
    return [str(raw) for raw in ids
            if (row := by_id.get(str(raw).strip().upper())) is not None
            and _is_open(row, disposed)]
