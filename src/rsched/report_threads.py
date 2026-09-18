"""Thread concentration on the report ledger: how many threads one sender has open to one
owner, and which rows a new report may fold into itself.

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

Both take the FOLDED rows (`reports.read_reports`) rather than a path — they are pure
questions about a stream the caller already holds under the ledger lock, and a second read
inside the lock would be a second answer to the same question.
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


def open_threads(rows: list[dict], *, routine: str, target: str) -> list[str]:
    """The report ids `routine` still has open to `target`, OLDEST FIRST (the ledger's own
    order), so the caller can name the one to fold into.

    Open means the owner still has it: not retracted, not superseded into another thread, not
    itself a closure, and not DISPOSED OF by any later report. That is the same reading
    `readmodels/items.py` derives a status from — computed here from the rows directly,
    because a WRITE precondition must never depend on a read model.

    Disposal has two faces and both count (D134): `answers` ends one exchange, `settles` ends
    every row it names. Counting only `answers` would keep a settled row in the cap's tally, so
    the cap would go on refusing new reports about work the ledger already knows is finished.
    """
    answered = {str(i).strip().upper()
                for r in rows if not r.get("retracted")
                for i in (str(r.get("answers") or ""), *(r.get("settles") or []))}
    return [str(r.get("id"))
            for r in rows
            if r.get("routine") == routine and r.get("target") == target
            and not r.get("retracted") and not r.get("superseded") and not r.get("closes")
            and str(r.get("id") or "").upper() not in answered]


def supersedable(rows: list[dict], ids: list[str]) -> tuple[list[str], list[str]]:
    """Split `ids` into those a new report may take over and those it may not, preserving the
    caller's order. Unusable means unknown to the ledger, already retracted, or already folded
    into some other thread — a row cannot be in two threads at once.
    """
    by_id = {str(r.get("id") or "").upper(): r for r in rows}
    ok: list[str] = []
    bad: list[str] = []
    for raw in ids:
        row = by_id.get(str(raw).strip().upper())
        (bad if row is None or row.get("retracted") or row.get("superseded") else ok).append(
            str(raw).strip().upper())
    return ok, bad


