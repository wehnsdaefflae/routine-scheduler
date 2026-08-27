"""The Decisions READ model — what is actually open, gathered from three different places.

Split out of `api_questions.py` (F393): the routes are one job, working out what belongs on the
page is another, and the second is where the subtlety lives. An open decision can be a run's
blocking question, a deferred ask, or a self-audit decision awaiting an answer, and each has its
own notion of "already answered" — a snooze, a durable answer record, a message the routine has
since consumed. Getting that wrong shows the operator a question they have already settled.
"""

# what counts as already decided — the read model's own vocabulary.
from __future__ import annotations

import re
from datetime import UTC, datetime

from .. import registry
from ..paths import read_json

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")

#: Statuses that mean the decision is already MADE — the report carries them so the Items
#: page can show progress, but they are not asks. `in_progress` is the big one: self-audit
#: is already building the thing, so queueing it for an answer is a card with nothing to
#: choose (2026-08-06: D74/D76/D77/D78/D80 all reached the operator this way in one night).
_DECIDED_STATUSES = frozenset({"settled", "closed", "done", "in_progress", "in progress",
                               "authorized", "shipped", "building"})



def _audit_decisions(server) -> list[dict]:
    """The self-audit report's OPEN decisions as meta-badged question items. A decision
    leaves the inbox when an answer is queued for it, when the report marks it decided
    (`_DECIDED_STATUSES` — or the routine's prose convention, a detail starting with
    SETTLED), or when it offers EXACTLY ONE option.

    The one-option rule is the load-bearing half: an already-decided item re-presented as
    a card whose only option restates the decision ("phase 1 next run") costs the operator
    a read and a click for no choice, and it is the shape self-audit reaches for whenever
    it wants an acknowledgment. Zero options stays open on purpose — that is a free-text
    ask, which the answer POST accepts. Nothing is hidden by this: every decision, at every
    status, is still listed on the Messages page (`readmodels.items`) with its status.
    """
    from ..readmodels.items import SELF_AUDIT_SLUG
    from .api_audit import queued_messages

    rdir = server.routines_home / SELF_AUDIT_SLUG
    report = read_json(rdir / "audit" / "report.json")
    if not isinstance(report, dict):
        return []
    queued = {m.group(1).strip() for p in queued_messages(rdir)
              if (m := _DECISION_RE.match(p.get("text") or ""))}
    # Durable answered markers: a mid-run delivery consumes the inbox message instantly,
    # so `queued` alone cannot keep an answered decision out of the inbox while the report
    # still lists it open — the user would be asked the same decision again and again.
    # A decision answered at-or-after this report's `generated` stays hidden until a NEWER
    # report explicitly lists it open again.
    answered = read_json(rdir / "audit" / "decisions-answered.json")
    if not isinstance(answered, dict):
        answered = {}
    out = []
    for d in report.get("decisions") or []:
        did = str(d.get("id") or "").strip()
        options = [str(o) for o in (d.get("options") or [])]
        settled = (str(d.get("status") or "").strip().lower() in _DECIDED_STATUSES
                   or str(d.get("detail") or "").lstrip().upper().startswith("SETTLED"))
        if not did or did in queued or settled or len(options) == 1:
            continue
        marker = str(answered.get(did) or "")
        if marker and marker >= str(report.get("generated") or ""):
            # answered since this report was written — not open again until a newer
            # report says so
            continue
        text = str(d.get("title") or did)
        if d.get("detail"):
            text += "\n\n" + str(d["detail"])
        out.append({"qid": f"audit:{did}", "routine": SELF_AUDIT_SLUG, "mode": "deferred",
                    "meta": True, "question": text, "options": options,
                    "asked": report.get("generated") or ""})
    return out

def _mark_answered(routine_dir, item: dict) -> dict:
    """The single answered-state derivation: an inbox answer file means the user has
    spoken, even while the pending file waits for the next run to consume it. Without
    this, an answered decision re-appears as open on every reload. The answer's source
    rides along so every surface can say WHERE the decision was made (web / discord).
    """
    ans = read_json(routine_dir / "inbox" / f"answer-{item.get('qid')}.json")
    if isinstance(ans, dict) and "text" in ans:
        item["answered"] = True
        item["answer"] = ans["text"]
        item["answer_source"] = ans.get("source", "web")
    return item

def _record_dir(server, match: dict):
    """The dir whose inbox/ and questions/pending/ the engine behind a decision actually
    polls — the decision's own routine/conversation/background-task dir.
    """
    if match.get("background"):
        return server.background_home / match["routine"]
    home = server.conversations_home if match.get("conversation") else server.routines_home
    return home / match["routine"]

def _all_questions(server, home_kind: str = "routine") -> list[dict]:
    """Open questions of one home's catalog. Conversation questions carry
    `conversation: True`, detached-task questions `background: True` (+ the owning
    conversation's slug as `owner`), so the answer endpoint and the UI can tell the
    homes apart. Background asks are deferred-only by design — surfacing them here is
    what lets the user see and answer them at all (the answer lands durably in the
    task's inbox).
    """
    home = {"routine": None, "conversation": server.conversations_home,
            "background": server.background_home}[home_kind]
    marker = {} if home_kind == "routine" else {home_kind: True}
    out: list[dict] = []
    for info in registry.scan(server, home).values():
        runs = {r.ts: r for r in info.runs}
        seen: set[str] = set()
        active = info.active_run
        if active and active.question:
            seen.add(str(active.question.get("qid")))
            item = {**active.question, "routine": info.slug, "mode": "blocking",
                    "run_id": active.run_id, "run_state": active.state,
                    "asked": active.question.get("asked") or active.ts, **marker}
            if home_kind == "background" and isinstance(info.cfg.owner, dict):
                item["owner"] = str(info.cfg.owner.get("slug") or "")
            out.append(_mark_answered(_record_dir(server, item), item))
        for q in info.open_questions:
            if str(q.get("qid")) in seen:
                continue   # a live blocking question also has a durable pending record
            # a blocking record with no live run behind it (crash/kill) is just deferred now
            mode = "deferred" if q.get("mode") == "blocking" else q.get("mode", "deferred")
            item = {**q, "routine": info.slug, "mode": mode, **marker}
            if home_kind == "background" and isinstance(info.cfg.owner, dict):
                item["owner"] = str(info.cfg.owner.get("slug") or "")
            # a deferred question's `asked` is the run_ts it was filed from — link back to
            # that run (with its live state) when the run dir still exists, so a stale
            # question is recognizable against what its run actually did.
            run = runs.get(str(q.get("asked") or ""))
            if run:
                item.setdefault("run_id", run.run_id)
                item["run_state"] = run.state
            out.append(_mark_answered(info.cfg.dir, item))
    return out

def open_decisions(server) -> list[dict]:
    """Every decision across the instance, one shape — the Decisions page, the badge, the
    tab-open notifier, and the Web Push sender all read this. A record snoozed into the
    future carries `snoozed: True` (still open, still visible to runs — hidden by default
    on the user surfaces only).
    """
    items = (_all_questions(server) + _all_questions(server, "conversation")
             + _all_questions(server, "background") + _audit_decisions(server))
    now = datetime.now(UTC)
    for item in items:
        if _snooze_active(item.get("snoozed_until"), now):
            item["snoozed"] = True
    return items

def _snooze_active(snoozed_until: object, now: datetime) -> bool:
    if not snoozed_until:
        return False
    try:
        until = datetime.fromisoformat(str(snoozed_until))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > now
