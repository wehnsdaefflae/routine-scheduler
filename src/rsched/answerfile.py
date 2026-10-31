"""The ONE writer of the inbox's SECOND shape: `inbox/answer-<qid>.json` (D143).

A routine's `inbox/` holds two record shapes, and until now only the first had an owner.
`engine/inbox.file_message` has been the one writer of `msg-*` since F499 unified seven
hand-rolled call sites; the answer shape kept exactly the habit that defect was about — four
modules spelling the filename themselves at seven sites (`web/api_questions` for the answer,
the answer-and-run-now rewrite and the defer marker, `web/api_question_decline`,
`web/api_patch_applied`, and `engine/inbox_questions.revise_answer`), each stamping its own
`ts`.

It is a SECOND writer rather than a second caller of the first, because the two shapes share a
directory and a consume-by-rename discipline and nothing else:

* `file_message` validates `via` against the closed `VIAS` set — the delivery-policy switch
  that decides WHEN a run consumes the file. An answer has no `via` and no delivery policy: it
  is matched by qid, by a pass of its own (`inbox_questions.collect_deferred_answers`).
* Freight BUYS a run; an answer must never buy one (`daemon/gate_prepare`, the operator's
  reversal of 2026-09-12 — an answer is work for a fire that already happened, never a reason
  to fire). Writing one through the message seam would file it under the stem every freight
  scanner selects.

So: two shapes, two writers, and `tests/test_policy_inbox_writer.py` allow-lists exactly these
two — a third direct write into `inbox/` is a test failure naming the seam to call.

WHAT THIS SEAM HOLDS, that seven call sites could not:

**A record neither reader would consume is refused at the write.** Both readers require a
`text` key (or a truthy `defer`): `take_answer` returns None without one, and
`collect_deferred_answers` logs "unreadable or has no text" and skips the file WITHOUT
consuming it. Such a file is therefore never read and never drained — it waits in the inbox
forever, and by the gate rule above does not even admit the run that might have noticed. The
write is the one place that is catchable, and `ValueError` there is a caller's bug, not an
operator's.

**A correction never resurrects a consumed answer.** `update` rewrites a file that is still
queued and creates nothing. The patch-applied route corrects two copies of a receipt (the
queued answer and the durable archive), and on the answer half a plain write would re-create a
record a run had already drained — the next run would then read a settled decision as fresh.
That is the answer-side twin of the race `inbox.rewrite_message` holds a lock for, where the
re-created file delivered one edit twice.

**One spelling of the filename, and the qid carried into the record.** Both readers fall back
to `path.stem.removeprefix("answer-")` when the record omits `qid`, so a disagreement between
the two would be consumed under one id and archived under the other.

The payload's FIELDS stay the callers' own (`decline.payload`, `patchreceipt.receipt`, the
API's answer body): this module owns where an answer lives and what makes it readable at all,
not what a particular channel has to say.
"""

from __future__ import annotations

from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json


def path(routine_dir: Path, qid: str) -> Path:
    """Where `qid`'s answer waits until a run consumes it.

    The one spelling of the filename — every reader of this directory globs `answer-*.json`
    and parses the qid back out of the stem.
    """
    return Path(routine_dir) / "inbox" / f"answer-{qid}.json"


def readable(record: object) -> bool:
    """Would either reader actually consume this record?

    The condition both of them apply, in one place: a `text` KEY (empty text is a real
    answer — an access request decided with no prose), or a truthy `defer` (the Decisions
    page's "continue on your stated default", which carries no text by design).
    """
    return isinstance(record, dict) and ("text" in record or bool(record.get("defer")))


def write(routine_dir: Path, qid: str, record: dict) -> Path:
    """File the answer to `qid`, atomically, and return the path it was written to.

    `record` is the channel's own payload — an answer body, `decline.payload`, a defer
    marker. The qid is carried into it and a `ts` is stamped unless the caller brought one
    (the API returns its payload to the person who clicked, so the time in the file and the
    time in the response must be the same one).

    Raises ValueError for a record no reader would consume: see the module note for why that
    is the one failure this seam exists to make loud.
    """
    if not readable(record):
        raise ValueError(
            f"answer record for {qid!r} carries neither `text` nor `defer` — no reader would "
            f"consume it (engine.inbox.take_answer returns None, collect_deferred_answers "
            f"skips it without consuming), so it would wait in the inbox unread and unnoticed")
    target = path(routine_dir, qid)
    atomic_write_json(target, {**record, "qid": qid, "ts": str(record.get("ts") or now_iso())})
    return target


def update(routine_dir: Path, qid: str, fields: dict) -> dict | None:
    """Correct an answer that is STILL QUEUED — the merged record, or None if none is.

    For the facts that become true after an answer was filed: the `approve & apply` button
    reporting that the patch really did go live, on a record a run has yet to read. Creates
    nothing, so correcting an answer a run already drained is a no-op the caller can see
    (None) rather than a re-filing that would deliver a settled decision to the next run.
    """
    current = read_json(path(routine_dir, qid))
    if not isinstance(current, dict) or not current:
        return None
    merged = {**current, **fields}
    atomic_write_json(path(routine_dir, qid), merged)
    return merged
