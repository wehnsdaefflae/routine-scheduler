"""The QUESTION half of a routine's inbox: asks a run filed, and the answers that come back.

Two filename shapes share the inbox directory and a consume-by-rename discipline (D143):

  msg-<stem>.json    freight for a run          -> engine/inbox
  answer-<qid>.json  a question's answer        -> HERE, matched by qid

This module owns the question LIFECYCLE — filing the ask, taking the answer, archiving the
pair, open/resolved. The answer file's one WRITER is `rsched/answerfile` (a separate seam
because an answer carries no `via` and must never buy a run);
`tests/test_policy_inbox_writer.py` allow-lists exactly the two writers.

`inbox` re-exports every public name below. Both halves consume by RENAME (`inbox._consume`)
— never a partial read — landing the consumed file under <run_dir>/consumed/.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import answerfile, decline, patchreceipt, proofs, withdraw
from ..ids import now_iso
from ..paths import read_json
from . import askclass
from .inbox import _consume

log = logging.getLogger("rsched.inbox")



def take_answer(routine_dir: Path, qid: str, consumed_dir: Path) -> dict | None:
    """The answer file for a specific question, if present (consumed on read). A defer
    marker (`{"defer": true}`, written by the Decisions page's defer-to-next-run action)
    is returned like an answer — the caller unblocks without a decision.
    """
    path = answerfile.path(routine_dir, qid)
    obj = read_json(path)
    if not isinstance(obj, dict) or ("text" not in obj and not obj.get("defer")):
        return None
    _consume(path, consumed_dir)
    return obj


def collect_deferred_answers(routine_dir: Path, consumed_dir: Path,
                             *, own_run_ts: str | None = None) -> list[dict]:
    """Match stray answer files against questions/pending/, consume both, and return
    [{question, answer}] — called at run start (boot digest) AND at every live turn
    boundary (control.drain_injections). An access-request pair also carries `request` +
    `decision` (+ `account`) so both consumers can seed the run overlay
    (requests.apply_deferred_decisions). A pair whose question carried a `config_patch`
    also carries `config_patch_applied` (+ `config_patch_target`), so the asking run learns
    whether its proposed change is LIVE.
    """
    inbox = routine_dir / "inbox"
    pending = routine_dir / "questions" / "pending"
    if not inbox.is_dir():
        return []
    pairs: list[dict] = []
    for path in sorted(inbox.glob("answer-*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("defer") and "text" not in obj:
            # a defer marker that outlived the run it targeted — its purpose is spent
            _consume(path, consumed_dir)
            continue
        if not isinstance(obj, dict) or "text" not in obj:
            log.warning("inbox: answer file %s is unreadable or has no text — skipping it",
                        path.name)
            continue
        qid = str(obj.get("qid") or path.stem.removeprefix("answer-"))
        if own_run_ts is not None and not qid.startswith(f"q-{own_run_ts}-"):
            # A RESUMED leg takes only answers to ITS OWN questions; another run's answer
            # waits for the next fresh run's boot digest.
            continue
        qfile = pending / f"{qid}.json"
        raw = read_json(qfile)
        was_pending = isinstance(raw, dict)
        # No pending record does NOT mean no question (a re-answer after consumption):
        # `_reanswered` recovers the ask from the archive; None means nothing was asked.
        q = raw if isinstance(raw, dict) else _reanswered(routine_dir, qid)
        if q is None:
            # The answer belongs to someone else (e.g. a blocking ask later in this very run,
            # whose record lives in the run's status.json). Leave it alone.
            continue
        pair = {"qid": qid, "question": q.get("question", "?"), "answer": str(obj["text"])}
        # Whether the question's `config_patch` was APPLIED (R2778).
        patchreceipt.carry(pair, obj)
        # Whether the operator DECLINED it — the `request` branch below covers access
        # requests only (R2197).
        decline.carry(pair, obj)
        # The decision's PROOF, chosen option moved on: settled only once it turns up (D220).
        proofs.carry(pair, {**q, "proof": proofs.answered(q.get("proof") or {},
                                                          str(obj["text"]).strip())})
        if q.get("request") and obj.get("decision"):
            pair["request"] = [str(r) for r in q["request"]]
            pair["decision"] = str(obj["decision"])
            if obj.get("account"):
                pair["account"] = str(obj["account"])
        pairs.append(pair)
        if obj.get("revised"):
            pair["revised"] = True
        _archive_answer(routine_dir, q, obj)
        _consume(path, consumed_dir)
        if was_pending:
            try:
                qfile.unlink()
            except OSError:
                pass
    return pairs


def _reanswered(routine_dir: Path, qid: str) -> dict | None:
    """The ASK behind an answer whose pending record is already gone (F763), from the
    durable archive — or None when nothing was ever asked under this qid.

    A pending record is unlinked when a run consumes its answer; an answer filed AFTER that
    is the operator's latest word and must still reach a run. The pair is marked `revised`
    only when the writer said so — this path does not invent that claim.
    """
    archived = read_json(_answered_dir(routine_dir) / f"{qid}.json")
    if not isinstance(archived, dict) or not archived.get("question"):
        return None
    if withdraw.withdrawn(archived):
        # F943: the asking routine withdrew this card, so the archive row is the WITHDRAWAL and
        # not an answer. Delivering the pair would hand a run an answer to a question its own
        # earlier turn took back, and archiving it would overwrite the withdrawal with
        # `answered: True` — losing the one record of what became of the card.
        return None
    return {k: v for k, v in archived.items()
            if k not in ("answered", "answer", "answer_source", "answered_ts",
                         "consumed", "first_consumed", "revised", "decision")}


def _answered_dir(routine_dir: Path) -> Path:
    return routine_dir / "questions" / "answered"


def _archive_answer(routine_dir: Path, question: dict, answer: dict) -> Path:
    """Write what consumption is about to destroy (F525).

    One record per question, the ASK and the ANSWER together, so the decision stays readable
    (and amendable) after the run that acted on it. Rewritten in place on a revision — one
    question, one row.
    """
    from ..paths import atomic_write_json

    qid = str(question.get("qid") or answer.get("qid") or "")
    path = _answered_dir(routine_dir) / f"{qid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = read_json(path)
    record = {**question, "qid": qid,
              "answered": True,
              "answer": str(answer.get("text") or ""),
              "answer_source": str(answer.get("source") or "web"),
              "answered_ts": str(answer.get("ts") or ""),
              "consumed": now_iso()}
    if answer.get("decision"):
        record["decision"] = str(answer["decision"])
    # The decline and the patch verdict ride along: the settled card is rebuilt from the
    # archive once a run has consumed the answer.
    decline.carry(record, answer)
    patchreceipt.carry(record, answer)
    if isinstance(prior, dict) and prior.get("consumed"):
        # an amended answer keeps the ORIGINAL settlement time: the decision was made then,
        # and only its wording moved
        record["first_consumed"] = str(prior.get("first_consumed") or prior["consumed"])
        record["revised"] = True
    atomic_write_json(path, record)
    return path


def answered_questions(routine_dir: Path) -> list[dict]:
    """Questions this routine's runs have ANSWERED and consumed — the durable settled
    record (F525), newest first: the pending record plus the answer, its source and when a
    run took it.
    """
    adir = _answered_dir(routine_dir)
    if not adir.is_dir():
        return []
    out = [obj for path in adir.glob("*.json")
           if isinstance(obj := read_json(path), dict) and obj.get("question")]
    out.sort(key=lambda r: str(r.get("consumed") or ""), reverse=True)
    return out


def revise_answer(routine_dir: Path, qid: str, text: str) -> Path:
    """Amend an answer the user already gave (F525).

    Still queued: the waiting answer file is rewritten, and the next run sees only the new
    text. Already consumed: the archived record supplies the question, the pair is re-queued
    (answer file + pending record restored) and the amendment reaches the NEXT run marked
    `revised`, because a correction no run ever reads is not a correction.

    Raises LookupError when nothing was ever answered under this qid — inventing a record
    would put words in the user's mouth.
    """
    from ..paths import atomic_write_json

    waiting = read_json(answerfile.path(routine_dir, qid))
    if isinstance(waiting, dict) and "text" in waiting:
        return answerfile.write(routine_dir, qid,
                                {**waiting, "text": text, "ts": now_iso(), "revised": True})
    archived = read_json(_answered_dir(routine_dir) / f"{qid}.json")
    if not isinstance(archived, dict) or not archived.get("question"):
        raise LookupError(f"no answer on record for {qid!r} — nothing to revise")
    if withdraw.withdrawn(archived):
        # F943: the routine that ASKED took this card back, so there is no answer to amend —
        # and re-queueing it would restore a pending record for a question whose premise is
        # gone, putting the withdrawn card back on the page as open.
        raise LookupError(f"{qid} was withdrawn by the routine that asked — there is no "
                          f"answer on record to revise")
    pending = routine_dir / "questions" / "pending" / f"{qid}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    record = {k: v for k, v in archived.items()
              if k not in ("answered", "answer", "answer_source", "answered_ts",
                           "consumed", "first_consumed", "revised", "decision")}
    atomic_write_json(pending, record)
    return answerfile.write(routine_dir, qid,
                            {"text": text, "revised": True,
                             "source": str(archived.get("answer_source") or "web")})


def file_question(routine_dir: Path, qid: str, question: str, options: list[str],  # noqa: PLR0913 — the ONE record shape: every field is a documented key of it, keyword-only
                  asked_ts: str, *, mode: str = "deferred", qtype: str = "question",
                  default: str = "", expires: str = "", config_patch: dict | None = None,
                  config_target: str = "", request: list[str] | None = None,
                  report: str = "", report_target: str = "", batch: str = "",
                  blocks_work: object = False, proof: dict | None = None) -> Path:
    """The ONE decision record every kind of required user feedback funnels into —
    plain asks, util approvals and access requests, deferred and blocking alike; every
    surface renders from this shape.

    `expires` (blocking: when the run continues without an answer; rewritten as deferred on
    timeout/abort); `config_patch` (+ `config_target`) for the one-click apply; `request`
    (grant-entity ids) makes an ACCESS REQUEST whose answer carries a `decision`;
    `report`/`report_target` mark a REPORT-BORN decision (F621) so the answer can be relayed
    to the owner; `batch` (D212) groups the records of ONE `ask_user` action. Every record
    carries its CLASS (`askclass.of`, `blocks_work` overriding) — ORTHOGONAL to `mode`: the
    class says whether the run could work without the answer, `mode` whether it waited.
    """
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    record: dict = {"qid": qid, "question": question, "options": options,
                    "asked": asked_ts, "mode": mode, "type": qtype,
                    "class": askclass.of(request=request, blocks_work=blocks_work)}
    if batch:
        record["batch"] = batch  # a lone ask carries none
    if default:
        record["default"] = default
    if expires:
        record["expires"] = expires
    if config_patch:
        record["config_patch"] = config_patch
    if config_target:
        # The patch is for ANOTHER routine, resolved and validated by engine/config_bridge
        # at ask time; absent means the asking routine.
        record["config_target"] = config_target
    if proof:
        # D220: per option, what would have to be TRUE for its answer to have been carried
        # out (`rsched/proofs.py`).
        record["proof"] = proofs.filed(proof)
    if request:
        record["request"] = list(request)
    if report:
        # The report id the answer is relayed under, and who owes the work once decided.
        record["report"] = report
        if report_target:
            record["report_target"] = report_target
    atomic_write_json(path, record)
    return path


def resolve_question(routine_dir: Path, qid: str) -> None:
    """Drop the pending record — the decision was made (or superseded by a re-ask)."""
    try:
        (routine_dir / "questions" / "pending" / f"{qid}.json").unlink(missing_ok=True)
    except OSError:
        pass


def open_questions(routine_dir: Path) -> list[dict]:
    """Pending questions. A question whose answer already waits in the inbox (answered on
    the Decisions page, not yet drained by a run) is flagged `answered: True` so every
    surface can show it as answered-and-queued instead of still-open.
    """
    pending = routine_dir / "questions" / "pending"
    if not pending.is_dir():
        return []
    inbox = routine_dir / "inbox"
    out = []
    for path in sorted(pending.glob("*.json")):
        obj = read_json(path)
        if isinstance(obj, dict) and obj.get("question"):
            qid = str(obj.get("qid") or path.stem)
            answer = read_json(inbox / f"answer-{qid}.json")
            if isinstance(answer, dict):
                obj = {**obj, "answered": True,
                       **({"ran_now": answer["ran_now"]} if answer.get("ran_now") else {})}
            out.append(obj)
    return out
