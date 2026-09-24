"""The QUESTION half of a routine's inbox: asks a run filed, and the answers that come back.

Split from `engine/inbox` (2026-09-24, D143) when that module reached 530 lines against the
repo's ~350 budget. The seam is the one the file always had: two filename shapes in the same
directory, sharing a consume-by-rename discipline and nothing else.

  msg-<stem>.json    freight for a run          -> engine/inbox
  answer-<qid>.json  a question's answer        -> HERE, matched by qid

What lives here is everything that knows what a QUESTION record is: filing one
(`file_question`), taking its answer (`take_answer`, `collect_deferred_answers`), archiving
the pair for the Decisions page (`_archive_answer`, `answered_questions`, `revise_answer`),
and the open/resolved lifecycle (`open_questions`, `resolve_question`).

`inbox` re-exports every public name below, so `inbox.file_question(...)` still resolves and
no caller moved: the split is an internal seam, not a new interface. Both halves consume by
RENAME (`inbox._consume`) — never a partial read — and land the consumed file under
<run_dir>/consumed/ for the audit trail.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..ids import now_iso
from ..paths import read_json
from .inbox import _consume

log = logging.getLogger("rsched.inbox")



def take_answer(routine_dir: Path, qid: str, consumed_dir: Path) -> dict | None:
    """The answer file for a specific question, if present (consumed on read). A defer
    marker (`{"defer": true}`, written by the Decisions page's defer-to-next-run action)
    is returned like an answer — the caller unblocks without a decision.
    """
    path = routine_dir / "inbox" / f"answer-{qid}.json"
    obj = read_json(path)
    if not isinstance(obj, dict) or ("text" not in obj and not obj.get("defer")):
        return None
    _consume(path, consumed_dir)
    return obj


def collect_deferred_answers(routine_dir: Path, consumed_dir: Path,
                             *, own_run_ts: str | None = None) -> list[dict]:
    """Match stray answer files against questions/pending/, consume both, and return
    [{question, answer}] — called at run start (boot digest) AND at every live turn
    boundary (control.drain_injections, the F195 delivery). An access-request pair also
    carries `request` (the record's entity ids) + `decision` (+ `account`), so both
    consumers can seed the run overlay (requests.apply_deferred_decisions) — an
    "allow now" decided between runs grants exactly the run that consumes it, and a
    decision landing mid-run reaches the running run's policy at once (R118).
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
            # F359: a RESUMED leg (the caller sets own_run_ts) takes only answers to ITS
            # OWN questions; an answer to another run's question waits for the next fresh
            # run, whose boot digest presents it with full context.
            continue
        qfile = pending / f"{qid}.json"
        q = read_json(qfile)
        if not isinstance(q, dict):
            # No matching pending question — the answer belongs to someone else (e.g. a
            # blocking ask later in this very run). Leave it alone.
            continue
        pair = {"qid": qid, "question": q.get("question", "?"), "answer": str(obj["text"])}
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
        try:
            qfile.unlink()
        except OSError:
            pass
    return pairs


def _answered_dir(routine_dir: Path) -> Path:
    return routine_dir / "questions" / "answered"


def _archive_answer(routine_dir: Path, question: dict, answer: dict) -> Path:
    """Write what consumption is about to destroy (F525).

    Consuming an answer used to remove BOTH the inbox file and the pending record and write
    nothing in their place — so the Decisions page, which derives "answered" by reading
    exactly those two, lost the card and the user's own words with it the moment a run
    booted. The archive is that missing half: one record per question, holding the ASK and
    the ANSWER together, so the decision stays readable (and amendable) after the run that
    acted on it. Rewritten in place on a revision — one question, one row, never a pile of
    versions.
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
    if isinstance(prior, dict) and prior.get("consumed"):
        # an amended answer keeps the ORIGINAL settlement time: the decision was made then,
        # and only its wording moved
        record["first_consumed"] = str(prior.get("first_consumed") or prior["consumed"])
        record["revised"] = True
    atomic_write_json(path, record)
    return path


def answered_questions(routine_dir: Path) -> list[dict]:
    """Questions this routine's runs have ANSWERED and consumed — the durable settled
    record (F525), newest first. Each row is the pending record it was filed from plus the
    answer, its source and when a run took it, so every surface can show what was decided
    instead of losing it to the boot that acted on it.
    """
    adir = _answered_dir(routine_dir)
    if not adir.is_dir():
        return []
    out = [obj for path in adir.glob("*.json")
           if isinstance(obj := read_json(path), dict) and obj.get("question")]
    out.sort(key=lambda r: str(r.get("consumed") or ""), reverse=True)
    return out


def revise_answer(routine_dir: Path, qid: str, text: str) -> Path:
    """Amend an answer the user already gave (F525) — the operator's "i want to revise it
    to add the other answers".

    Still queued: the waiting answer file is rewritten, and the next run sees only the new
    text. Already consumed: the archived record supplies the question, the pair is re-queued
    (answer file + pending record restored) and the amendment reaches the NEXT run marked
    `revised`, because a correction no run ever reads is not a correction.

    Raises LookupError when nothing was ever answered under this qid — inventing a record
    would put words in the user's mouth.
    """
    from ..paths import atomic_write_json

    answer_path = routine_dir / "inbox" / f"answer-{qid}.json"
    waiting = read_json(answer_path)
    if isinstance(waiting, dict) and "text" in waiting:
        answer_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(answer_path, {**waiting, "text": text, "ts": now_iso(),
                                        "revised": True})
        return answer_path
    archived = read_json(_answered_dir(routine_dir) / f"{qid}.json")
    if not isinstance(archived, dict) or not archived.get("question"):
        raise LookupError(f"no answer on record for {qid!r} — nothing to revise")
    pending = routine_dir / "questions" / "pending" / f"{qid}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    record = {k: v for k, v in archived.items()
              if k not in ("answered", "answer", "answer_source", "answered_ts",
                           "consumed", "first_consumed", "revised", "decision")}
    atomic_write_json(pending, record)
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(answer_path, {"qid": qid, "text": text,
                                    "source": str(archived.get("answer_source") or "web"),
                                    "ts": now_iso(), "revised": True})
    return answer_path


def file_question(routine_dir: Path, qid: str, question: str, options: list[str],  # noqa: PLR0913 — the ONE record shape: every field is a documented key of it, keyword-only
                  asked_ts: str, *, mode: str = "deferred", qtype: str = "question",
                  default: str = "", expires: str = "", config_patch: dict | None = None,
                  config_target: str = "", config_home: str = "",
                  request: list[str] | None = None) -> Path:
    """The ONE decision record every kind of required user feedback funnels into —
    plain asks, util approvals and access requests, deferred and blocking alike. Blocking
    records carry `expires` (when the run continues without an answer) and are rewritten
    as deferred on timeout/abort; `config_patch` (a proposed routine.yaml change a revise
    run can't make itself) rides along for the Decisions page's one-click apply; `request`
    (grant-entity ids, entities.py) makes the record an ACCESS REQUEST — the Decisions
    page renders the four allow/deny × now/forever buttons and the answer carries a
    `decision`. Every surface (Decisions page, run view, Discord mirror) renders from
    this shape.
    """
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    record: dict = {"qid": qid, "question": question, "options": options,
                    "asked": asked_ts, "mode": mode, "type": qtype}
    if default:
        record["default"] = default
    if expires:
        record["expires"] = expires
    if config_patch:
        record["config_patch"] = config_patch
    if config_target:
        # D123/F458: the patch is for ANOTHER routine. Resolved and validated in
        # engine/interact.py at ask time, so the Decisions page can PATCH this slug
        # directly; absent means the patch is for the asking routine, as before.
        record["config_target"] = config_target
    if config_home:
        # R1488: WHICH config surface that target lives on — "routines" or "domains". The
        # page used to derive the home from the ASKER's kind alone, which is why a domain
        # could never be a target: there was nowhere to say so. Resolved at ask time with
        # the target, so the apply button never has to guess the URL it posts to.
        record["config_home"] = config_home
    if request:
        record["request"] = list(request)
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
