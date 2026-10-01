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
