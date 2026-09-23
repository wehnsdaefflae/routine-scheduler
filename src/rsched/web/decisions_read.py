"""The Decisions READ model — what is actually open, gathered from three different places.

Split out of `api_questions.py` (F393): the routes are one job, working out what belongs on the
page is another, and the second is where the subtlety lives. An open decision can be a run's
blocking question, a deferred ask, or a self-audit decision awaiting an answer, and each has its
own notion of "already answered" — a snooze, a durable answer record, a message the routine has
since consumed. Getting that wrong shows the operator a question they have already settled.

MEMOIZED per home behind a stat fingerprint of every source that can change the answer
(`_sources`), with the audit report's decisions memoized on their own three inputs. This is
the most-fetched read model in the console — the badge, the tab-open notifier, the activity
feed, the run view and the Decisions page each refetch it on every bus event, once per open
tab — and each call walked all three homes' catalogs from disk (`registry.scan`: every
routine, every run's status.json, a deep copy of every config — 265 ms of Python with warm
registry memos). On 2026-09-14 three runs booting at once put 10-20 copies in flight at
7-25 s each and starved every other request until the settings page would not open. A call
over unchanged sources now costs one stat pass over Path objects built once (milliseconds),
and a burst of identical misses computes once (`memo`'s single-flight).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from .. import registry
from ..engine import inbox
from ..paths import read_json
from ..readmodels import memo

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")

#: Settled decisions per routine on the page (F525). The archive is durable and grows for
#: the life of the instance; the page shows the recent tail, and the full history stays
#: readable in `questions/answered/`.
SETTLED_CAP = 25

#: Statuses that mean the decision is already MADE — the report carries them so the Items
#: page can show progress, but they are not asks. `in_progress` is the big one: self-audit
#: is already building the thing, so queueing it for an answer is a card with nothing to
#: choose (2026-08-06: D74/D76/D77/D78/D80 all reached the operator this way in one night).
_DECIDED_STATUSES = frozenset({"settled", "closed", "done", "in_progress", "in progress",
                               "authorized", "shipped", "building"})



def _audit_decisions(server) -> list[dict]:
    """The self-audit report's open decisions (`_audit_decisions_fresh`), reused while the
    report, the durable answered-markers and the inbox (the queued answers) are unchanged.
    """
    from ..readmodels.items import SELF_AUDIT_SLUG

    rdir = server.routines_home / SELF_AUDIT_SLUG
    return memo.memoized(f"decisions:audit:{rdir}",
                         [rdir / "audit" / "report.json",
                          rdir / "audit" / "decisions-answered.json", rdir / "inbox"],
                         lambda: _audit_decisions_fresh(rdir))


def _audit_decisions_fresh(rdir: Path) -> list[dict]:
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


_HOME_ATTR = {"routine": "routines_home", "conversation": "conversations_home",
              "background": "background_home"}


def _all_questions(server, home_kind: str = "routine") -> list[dict]:
    """Open questions of one home's catalog (`_all_questions_fresh`), reused while none of
    the home's `_sources` changed. Callers get a copy — `open_decisions` marks snoozes on it
    and the answer routes look records up in it — so the cached list is never touched.
    """
    home: Path = getattr(server, _HOME_ATTR[home_kind])
    return memo.memoized(f"decisions:{home_kind}:{home}", _sources(home),
                         lambda: _all_questions_fresh(server, home_kind, home))


def file_backed_questions(server) -> list[dict]:
    """Every open FILE-BACKED question, across all three homes. One spelling of the
    three-home loop: a fourth home (or a renamed one) is one edit, not four, and `_HOME_ATTR`
    stays the single enumeration of the kinds.
    """
    return [q for kind in _HOME_ATTR for q in _all_questions(server, kind)]


def find_question(server, qid: str) -> dict | None:
    """The open file-backed record for `qid`, or None. Audit decisions live in the report
    rather than as records (they can be neither snoozed nor deferred), so they are not here.
    """
    return next((q for q in file_backed_questions(server) if q.get("qid") == qid), None)


def _sources(home: Path) -> list[Path]:
    """Every path whose change can alter one home's open-question list — the memo's inputs,
    stat'd BEFORE the walk so a change during it misses next time:

    - the home dir itself (a routine dir created or deleted stamps it) and EVERY subdir's
      `routine.yaml`, present or missing — whether the dir is a routine at all, its slug, a
      background task's `owner`; listed for dirs without one too, so a yaml landing after
      its mkdir is a missing→present transition and not an unseen file;
    - `questions/pending` and `inbox` — a new, rewritten or snoozed record, an answer file:
      every write there is an atomic rename INTO the dir, which stamps the dir's mtime (the
      registry's own `_open_questions_memo` rests on the same fact);
    - `questions/answered` — the durable settled records (F525). Listed for the same reason
      as the other two: a run consuming an answer, or the user revising one, lands a write
      there, and a page still serving the pre-consumption list would show a decision as open
      that has been settled (or miss an amendment the user just made);
    - `runs/` (a run armed, a run pruned) and every run's `status.json` — the live run's
      state and blocking question, the `run_state` a deferred record links back to. The
      FILE, not its dir: an atomic rewrite changes the inode, so no two writes can share a
      fingerprint the way two renames inside one clock tick could share a dir mtime.

    The listings are themselves memoized on the dir they list (home → its subdirs, each
    `runs/` → its status files), so a warm check is one stat per path over Path objects
    built once — ~1 400 stats for 134 dirs and 726 runs — and never a `pathlib` walk.
    """
    subdirs = memo.memoized_shared(f"decisions:dirs:{home}", [home], lambda: _subdirs(home))
    out = [home]
    for d, static in subdirs:
        runs = d / "runs"
        out += static
        out += memo.memoized_shared(f"decisions:runs:{runs}", [runs], partial(_status_files, runs))
    return out


def _subdirs(home: Path) -> list[tuple[Path, list[Path]]]:
    if not home.is_dir():
        return []
    return [(d, [d / "routine.yaml", d / "questions" / "pending", d / "inbox", d / "runs",
                 d / "questions" / "answered"])
            for d in sorted(home.iterdir()) if d.is_dir() and not d.name.startswith(".")]


def _status_files(runs: Path) -> list[Path]:
    if not runs.is_dir():
        return []
    return [r / "status.json" for r in sorted(runs.iterdir()) if r.is_dir()]


def _all_questions_fresh(server, home_kind: str, home: Path) -> list[dict]:
    """Open questions of one home's catalog. Conversation questions carry
    `conversation: True`, detached-task questions `background: True` (+ the owning
    conversation's slug as `owner`), so the answer endpoint and the UI can tell the
    homes apart. Background asks are deferred-only by design — surfacing them here is
    what lets the user see and answer them at all (the answer lands durably in the
    task's inbox).
    """
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
        # F525: decisions a run has ALREADY consumed. Until the durable archive existed,
        # consumption deleted both the pending record and the answer file, so an answered
        # card — and the user's own words in it — vanished at the next boot; the operator
        # hit exactly that ("why can't i see this answer... i want to revise it"). These
        # are settled, never open: they sort into the page's Settled group and carry
        # `settled: True` so nothing counts them as work owed.
        for rec in inbox.answered_questions(info.cfg.dir)[:SETTLED_CAP]:
            if str(rec.get("qid")) in seen:
                continue
            out.append({**rec, "routine": info.slug, "mode": "deferred",
                        "settled": True, **marker})
    return out

def open_decisions(server) -> list[dict]:
    """Every QUESTION-shaped decision across the instance, one shape — the Decisions page, the
    badge, the tab-open notifier, and the Web Push sender all read this. A record snoozed into
    the future carries `snoozed: True` (still open, still visible to runs — hidden by default
    on the user surfaces only). The four parts are memoized (module docstring); the snooze
    mark is the one clock-dependent step, so it is applied to the copies on every call.

    It is NOT yet everything a person has to decide. Queued PROPOSALS — a routine a scheduled
    run designed, a lane change, a met goal, a library commit that broke a routine — are a
    second record shape in `.control/pending-creations/` (`rsched/pending.py`) with its own
    route and its own band on the page, and they are absent here: the badge and the push
    sender therefore count none of them. Both stores answer "what needs a person", so the end
    state is ONE — a proposal filed as a `type: "proposal"` question the existing approve path
    settles — and until the page stops rendering its own band, adding them here would show the
    operator every proposal twice.
    """
    items = file_backed_questions(server) + _audit_decisions(server)
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
