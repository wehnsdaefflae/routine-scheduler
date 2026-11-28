"""A question's two WITHDRAWALS — the asking routine takes its own card back (F943).

A decision could be answered, declined, snoozed or deferred, and every one of those is the
OPERATOR acting. None of them is available to the run that FILED the card, and that is the
case this module exists for: a premise usually breaks inside the run that asked. A routine
filed a decision, disproved its own premise an hour later, and could only file a SECOND card
correcting the first — so the Decisions page carried two cards for one matter, with the stale
one looking exactly as current as the correction.

Reports have had both dispositions all along (`rsched/reports.py`): an addressed report the
recipient has not consumed can be RETRACTED, and a later report can SUPERSEDE existing rows,
each folded row stamped with the carrier that took it over. This is that pair for questions,
and deliberately the same two field shapes, so the Decisions page renders a withdrawn card
the way `readmodels/item_reports.py` already renders a dropped or folded report:

    retracted      {"ts": …, "reason": …}          the premise is gone; nothing replaces it
    superseded_by  {"ts": …, "by": "<qid>", "reason": …}   that card is the live one now

A WITHDRAWAL IS NOT AN ANSWER, and the distinction is the whole design. A decline writes the
ordinary `inbox/answer-<qid>.json` payload, because the operator really did say something and
the asking run must read it. Withdrawing writes NO answer file: nobody spoke, so there is
nothing for `collect_deferred_answers` to deliver, and a run told "your question was answered"
when its own earlier turn withdrew it would act on words that do not exist. The record moves
instead — out of `questions/pending/` and into the durable archive every settled card is
rebuilt from (`engine/inbox_questions.answered_questions`) — carrying `withdrawn: True` and
the stamp above in place of an answer. One live card per matter, the other kept as history.

Lives at the top level, not under `web/`, for `decline.py`'s reason: the web layer WRITES
these fields and the engine READS them (`engine/inbox_questions` refuses to revise a
withdrawn record back into the open set), and `engine/` imports nothing from `web/`.
"""

from __future__ import annotations

from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

#: The record key both dispositions set, for every reader that only needs to know THAT the
#: card is gone from the open set — the page's settled branch, the toolbar's counts.
WITHDRAWN = "withdrawn"

#: Retraction: the asking routine withdrew the card and nothing takes its place.
RETRACTED = "retracted"

#: Supersession: another question is the live card for this matter. `by` names it.
SUPERSEDED_BY = "superseded_by"


def _archive_dir(routine_dir: Path) -> Path:
    return routine_dir / "questions" / "answered"


def stamp(reason: str = "", by: str = "") -> dict:
    """One event stamp — `{"ts", "reason"}`, plus `by` for a supersession.

    The same dict shape the reports ledger folds onto a row, so a reader that already knows
    how to render a dropped or folded report needs nothing new to render a withdrawn card.
    """
    note = str(reason or "").strip()
    return {"ts": now_iso(), "reason": note, **({"by": str(by)} if by else {})}


def withdrawn(obj: object) -> bool:
    """True when this record / API row was withdrawn by the routine that asked."""
    return isinstance(obj, dict) and bool(obj.get(WITHDRAWN))


def carrier_of(obj: dict) -> str:
    """The qid that took this matter over, or "" — what makes the history row navigable."""
    event = obj.get(SUPERSEDED_BY)
    return str(event.get("by") or "") if isinstance(event, dict) else ""


def record(routine_dir: Path, qid: str, question: dict, event: dict,
           *, key: str = RETRACTED) -> Path:
    """Move one OPEN record into the durable archive as withdrawn, and return its path.

    The archive is where every settled card is rebuilt from, so this is what puts the reason
    in front of the operator in place of the card. `answered` is deliberately absent: the
    question was never answered, and a row claiming otherwise would offer a revise control
    over an answer nobody gave.

    An existing archive row for this qid is NOT overwritten — that row is a real answer the
    operator gave (the caller refuses the withdrawal before reaching here), and the archive
    is the only remaining copy of it.
    """
    path = _archive_dir(routine_dir) / f"{qid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = read_json(path)
    if isinstance(prior, dict) and prior.get("answered"):
        raise ValueError(f"{qid} already has an answer on record — it is settled, "
                         f"not withdrawable")
    atomic_write_json(path, {**question, "qid": qid, WITHDRAWN: True, key: event,
                             "consumed": event["ts"]})
    return path


def drop_pending(routine_dir: Path, qid: str) -> None:
    """Take the card off the Decisions page — the archive row above is now its only home.

    After the archive write, never before: a withdrawal that lost the record instead of
    moving it would leave the operator a card that silently vanished.
    """
    (routine_dir / "questions" / "pending" / f"{qid}.json").unlink(missing_ok=True)
