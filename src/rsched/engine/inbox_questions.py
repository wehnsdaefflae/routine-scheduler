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

from .. import decline, patchreceipt
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

    A pair for a question that carried a `config_patch` also carries
    `config_patch_applied` (+ `config_patch_target`), so the asking run learns whether the
    change it proposed is LIVE — R2778, where a run read an affirmative answer, recorded the
    raise as applied, and planned 600 turns against a ceiling that was still 400.
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
        raw = read_json(qfile)
        was_pending = isinstance(raw, dict)
        # F763: no pending record does NOT mean no question — an answer filed after a run
        # consumed the first one has had its record unlinked already, and skipping it here is
        # what left eight answers unread across the fleet. `_reanswered` recovers the ask from
        # the durable archive; None means nothing was ever asked under this qid.
        q = raw if isinstance(raw, dict) else _reanswered(routine_dir, qid)
        if q is None:
            # The answer belongs to someone else (e.g. a blocking ask later in this very run,
            # whose record lives in the run's status.json). Leave it alone.
            continue
        pair = {"qid": qid, "question": q.get("question", "?"), "answer": str(obj["text"])}
        # R2778: and whether the `config_patch` the question carried was APPLIED. The run
        # that asked for a 600-turn budget read only the operator's "Ok", recorded the raise
        # as applied, and planned against a ceiling that was still 400.
        patchreceipt.carry(pair, obj)
        # R2197: …and whether the operator DECLINED it (rsched/decline.py). The access-request
        # branch below carries `decision` only for a record with `request` ids, so a refusal of
        # an ordinary proposal reached the run as prose and nothing else — which is the whole
        # thing the decline verb exists to stop.
        decline.carry(pair, obj)
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

    A pending record is unlinked the moment a run consumes its answer. An answer filed
    AFTER that (the operator answering the same card again, or the Decisions page's own
    catalog serving a record a boot consumed seconds earlier) had nothing left to match, so
    the loop skipped it and no run ever read it: eight such files were sitting across the
    fleet when this was found, the oldest 41 days and 15 runs old, including two where the
    second answer was the SPECIFIC one (`config-optimizer` q-20261009-020522-41, archived
    answer "approved & applied the proposed config change" at 04:41:29, inbox answer
    "Apply — add the 7-day heartbeat" at 04:41:37). A later answer is the operator's latest
    word, which is precisely what must reach the run.

    The archive is the same source `revise_answer` re-queues from, and the pair is marked
    `revised` by the caller's own `obj.get("revised")` only when the writer said so — this
    path does not invent that claim.
    """
    archived = read_json(_answered_dir(routine_dir) / f"{qid}.json")
    if not isinstance(archived, dict) or not archived.get("question"):
        return None
    return {k: v for k, v in archived.items()
            if k not in ("answered", "answer", "answer_source", "answered_ts",
                         "consumed", "first_consumed", "revised", "decision")}


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
    # R2197: a DECLINE's reason rides with its value, for the same reason the patch verdict
    # does below — the archive is what the settled card is rebuilt from once a run has
    # consumed the answer, so a refusal whose reason stopped here would lose the operator's
    # own words at exactly the boot that acted on it.
    decline.carry(record, answer)
    # R2778: and the patch verdict, or it survives only until the run that reads the answer —
    # after which the settled card would offer `approve & apply` again for a change already
    # live, and no reader of the archive could tell the two states apart.
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
                  config_target: str = "", request: list[str] | None = None,
                  report: str = "", report_target: str = "", batch: str = "",
                  blocks_work: object = False) -> Path:
    """The ONE decision record every kind of required user feedback funnels into —
    plain asks, util approvals and access requests, deferred and blocking alike. Blocking
    records carry `expires` (when the run continues without an answer) and are rewritten
    as deferred on timeout/abort; `config_patch` (a proposed routine.yaml change a revise
    run can't make itself) rides along for the Decisions page's one-click apply; `request`
    (grant-entity ids, entities.py) makes the record an ACCESS REQUEST — the Decisions
    page renders the four allow/deny × now/forever buttons and the answer carries a
    `decision`. `report`/`report_target` make it a REPORT-BORN decision (F621): the `R<n>` the
    asking run filed and the routine that owns the work, so the answer can be relayed to that
    owner and every surface can say which report is waiting. `batch` (D212) is the id every
    record filed by ONE `ask_user` action shares, so a surface can tell they were asked
    together; `blocks_work` is the asking run's class override. Every record carries its
    CLASS — `blocking` or `closing`, derived by `askclass.of` from its subject and that
    override — which is ORTHOGONAL to `mode`: the class says whether the asking run could do
    its work without the answer, `mode` whether its process sat and waited for one. Every
    surface (Decisions page, run view, Discord mirror) renders from this shape.
    """
    from ..paths import atomic_write_json

    path = routine_dir / "questions" / "pending" / f"{qid}.json"
    record: dict = {"qid": qid, "question": question, "options": options,
                    "asked": asked_ts, "mode": mode, "type": qtype,
                    "class": askclass.of(request=request, blocks_work=blocks_work)}
    if batch:
        # D212: the questions ONE ask_user action asked together. A surface groups them by it;
        # a lone ask carries no batch, because it was asked together with nothing.
        record["batch"] = batch
    if default:
        record["default"] = default
    if expires:
        record["expires"] = expires
    if config_patch:
        record["config_patch"] = config_patch
    if config_target:
        # D123/F458: the patch is for ANOTHER routine. Resolved and validated in
        # engine/config_bridge.py at ask time, so the Decisions page can PATCH this slug
        # directly; absent means the patch is for the asking routine.
        record["config_target"] = config_target
    if request:
        record["request"] = list(request)
    if report:
        # F621: the decision was born as a REPORT. The id is what the answer is relayed
        # under, and the target is who owes the work once he has decided — an answer with
        # neither would reach the page and then have nowhere to go.
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
