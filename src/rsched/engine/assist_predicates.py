"""The predicate registry — the deterministic checks a rule's `assists:` block names.

A library document declares a check by NAME; the implementation lives here, because a rule is
prose in a git-synced multi-writer directory and must never be able to ship code.

Every predicate is a pure question about the SITUATION — the action just emitted, the
observation just returned, what this run has done so far — and never about the model's
reasoning, which is not in the trace and is what makes compliance-checking impossible in the
first place (see `rsched/assists.py`). Each declares the MOMENT it answers at, so the linter
can refuse a rule that asks a pre-finish question at an observation.

Precision is the whole budget here. A line surfaced at the wrong moment is worse than no line
at all: the run trusts a fired assist to be relevant, and a layer that cries wolf is ignored
exactly when it is right. Prefer a predicate that misses to one that guesses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Situation:
    """Everything a predicate may look at. Fields absent at a moment are None."""

    loop: Any
    action: dict | None = None      # the action just emitted (observation, pre-finish)
    obs: dict | None = None         # the observation just returned (observation)

    @property
    def ctx(self):
        return self.loop.ctx


@dataclass(frozen=True)
class Predicate:
    moment: str
    check: Callable[[Situation], bool]
    describes: str      # what fired, shown to the model beside the rule's line


def failure_key(action: dict) -> str:
    """What makes two failures "the same call": the kind and the thing it acted on. A util
    or script is identified by its name, not its arguments — a second attempt with different
    flags at the same util that failed is the retry the rule is about.
    """
    from .actionschema import brief_value

    kind = str(action.get("kind") or "")
    name = action.get("name") if kind in ("util", "script") else brief_value(action)
    return f"{kind}:{name or ''}"


def _repeated_failure(s: Situation) -> bool:
    """The same call has now failed twice in this run.

    fix-the-cause's moment. The first failure is information and the run reads it; the
    SECOND of the same call is where a run either changes route or starts engineering around
    a wall — and it was the moment the old first-failure reminder missed: it fired 472 times
    for an identical retry that happened after 0.3% of failures. `loop.failures` is tallied
    by the loop from the same `is_failure` classifier, so this asks only whether the count
    for this call has just reached two.
    """
    from .observations import is_failure

    if s.obs is None or s.action is None or not is_failure(s.obs):
        return False
    return int((getattr(s.loop, "failures", None) or {}).get(failure_key(s.action), 0)) == 2


def _capability_denied(s: Situation) -> bool:
    """A permission refused this run something it wanted; the run moved on without
    asking for it.

    ask-policy's moment. A refusal either happens at the validation seam — the action never
    runs, the retry cycle corrects it (`ctx.last_denial_turn` marks the turn it cost) — or
    comes back as the observation itself, as an fs or secret gate does. Both end in the ONE
    wording `GrantPolicy.request_route` renders for an undecided entity, which is what makes
    the observation half checkable without guessing. A run whose very next action files the
    request has already done what the line says, so it is left alone.
    """
    from ..grantpolicy import REQUEST_ROUTE_MARK

    action = s.action or {}
    if action.get("kind") == "ask_user" and action.get("request"):
        return False
    if int(getattr(s.ctx, "last_denial_turn", 0) or 0) == int(s.ctx.turn or 0):
        return True
    error = str((s.obs or {}).get("error") or "")
    return REQUEST_ROUTE_MARK in error


def _user_corrected(s: Situation) -> bool:
    """A user message reached this run since the last boundary.

    fix-the-cause's moment: an intervention has just landed; the question the rule asks
    — what standing preference does this correction imply? — is answerable now and stale
    later.
    `ctx.user_replies` counts the user's own utterances (a delivered report is a routine's
    message and does not count), so this is the arrival edge without new bookkeeping.
    """
    seen = getattr(s.loop, "assist_user_replies", 0)
    return int(getattr(s.ctx, "user_replies", 0) or 0) > int(seen or 0)


def _file_writes(s: Situation) -> list[str]:
    """The paths this run wrote or edited, from `turn_records`.

    `turn_records` is the run history that SURVIVES compaction — the message list does not,
    so a predicate that greps scrollback silently stops working on exactly the long runs that
    need it most. A `brief` is the action's identifying field, whole (`compaction.turn_record`).
    """
    return [str(r.get("brief") or "")
            for r in getattr(s.loop, "turn_records", []) or []
            if r.get("kind") in ("write_file", "edit_file")]


def _ledger_untouched(s: Situation) -> bool:
    """This run changed something a reader will find later, and recorded no reasoning for it.

    decision-record's moment, and the narrowing matters more than the check. The rule is one
    of DEFAULT_RULES, so a predicate that fired on every ledger-less run would take a turn
    from every routine in the instance, every run — and most runs have nothing to record. A
    reminder that frequent is not a reminder; it is rent, and the layer stops being read.

    So it asks the question the rule asks — "keep the reasoning the ARTEFACTS cannot carry" —
    and fires only when this run produced an artefact to reason about: a write outside its own
    `state/` scratch, which is working state and not something a later reader interprets.

    Never in a conversation. A conversation's product is the reply, its reasoning is in the
    thread where the user can already see it, and its spine is `state/plan.md` rather than a
    ledger — so holding one's reply for a ledger entry costs the user a turn for nothing.

    Whether the ledger was written is read off the FILE — its mtime against the run's start
    — never off the actions: an append through a shell heredoc, a script or a util is
    invisible to `turn_records`; reading the actions made 48 of 75 of this assist's
    deferrals false.
    """
    from .runkind import is_conversation

    ledger = s.ctx.routine.dir / "LEDGER.md"
    if not ledger.is_file():
        return False        # a routine that keeps no ledger is not being asked to start one
    if is_conversation(s.ctx):
        return False
    try:
        if ledger.stat().st_mtime >= _run_started(s.ctx):
            return False    # the reasoning was recorded, by whatever means
    except OSError:
        return False
    return any(not path.startswith("state/") for path in _file_writes(s))


def _run_started(ctx) -> float:
    """The run's start as an epoch — `run_ts` is its local wall-clock stamp and survives a
    resume, where the engine's own monotonic clock restarts.
    """
    import datetime as _dt

    stamp = _dt.datetime.strptime(str(ctx.run_ts), "%Y%m%d-%H%M%S").astimezone()
    return stamp.timestamp()


def _uncheckpointed_repo_write(s: Situation) -> bool:
    """This action is about to edit a file inside a git repo the engine does not version.

    git-checkpoint's moment, and the one the design note reserves the HOLD rung for: the cost
    of skipping is irreversible in a way a reminder afterwards cannot undo. The engine
    autocommits the routine's OWN directory at run end, so that tree always has an undo point;
    a project repo the routine was granted a write root into has none unless the run makes one.

    Fires on the FIRST such write only — the one-fire-per-run rule is what makes "no checkpoint
    yet" true without having to detect a checkpoint commit, which happens inside a util or a
    shell command where the engine sees a command string and an exit code, nothing more.

    A repo found CLEAN is an undo point for the rest of the run, so it is remembered
    (`loop.assist_undo_points`) and never asked about again: HEAD restores what the run found.
    Asked afresh, the run's own first edit read as uncommitted work and its second edit into
    the same clean repo was held — and every edit paid a `git status` until one was. "The rest
    of the run" spans its legs: the finding is named on this write's observation
    (`assist.recorded`), which a resumed leg rebuilds the set from (engine/guardscope.py).
    """
    action = s.action or {}
    if action.get("kind") not in ("write_file", "edit_file"):
        return False
    target = str(action.get("path") or "").strip()
    if not target:
        return False
    from ..paths import expand, within

    path = expand(target)
    if not path.is_absolute():
        return False        # relative paths resolve inside the routine dir, which is versioned
    routine_dir = s.ctx.routine.dir
    if within(routine_dir, path) or path == routine_dir:
        return False        # the engine commits this tree itself at run end
    repo = next((p for p in [path, *path.parents] if (p / ".git").exists()), None)
    if repo is None or repo in s.loop.assist_undo_points:
        return False
    if _dirty(repo):
        return True
    s.loop.assist_undo_points.add(repo)
    s.loop.assist_undo_found = repo
    return False


def _dirty(repo) -> bool:
    """Does this working tree hold changes its HEAD cannot restore? A clean tree IS an undo
    point — 54 of the old hold's 102 fires were repos clean at HEAD, each overridden, so the
    run had learned to click past the one hold that guards an irreversible write. A tree git
    cannot read is treated as dirty: this is the hold that must not miss. It asks through
    `libgit.git`, whose reads never take the index lock — this status runs in the operator's
    own project repos, where a lock left behind would stop the operator's next commit.
    """
    import subprocess

    from .. import libgit

    try:
        out = libgit.git(repo, "status", "--porcelain", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return out.returncode != 0 or bool(out.stdout.strip())


def _asks_piling_up(s: Situation) -> bool:
    """This run has thrown several decisions over the wall without answers coming back.

    ask-policy's moment. `ctx.asks_deferred` is the engine's own churn telemetry — a deferred
    ask is a decision the run could not make and did not wait for — so a run accumulating them
    is the exact shape the rule is about: exhaust your own reach first, then defer a JUDGMENT,
    not a lookup.
    """
    return int(getattr(s.ctx, "asks_deferred", 0) or 0) >= _ASK_PILEUP


def _unclosed_delivered_report(s: Situation) -> bool:
    """This run was handed work by another routine and is ending without answering it.

    problem-routing's receiving half, and D131's moment (operator 2026-09-14: closing the
    thread is part of SHIPPING the fix, not of the next audit's bookkeeping). The rule already
    says it — "a hand-off nobody replied to is not settled, it is lost" — and the ledger
    measured what saying it was worth: F486 found four COMPLETED fixes sitting as the oldest
    open rows for eleven days, because a finish summary is not a closure.

    `ctx.reports_open` is the engine's own bookkeeping, not a scan: the drain records what it
    delivered and the report handler removes each id an `answers` closes. So this fires exactly
    when the run still owes a reply, and never when it has already sent one. A closure
    (`closes: true`) is never owed a reply and never enters the list.

    The finish is the only moment this can be said at — after it, the run that did the work is
    gone and the only thing left that could close the row is a person reading the ledger.

    What is still owed is re-read from the LEDGER at the moment of asking (R2086): the run's
    own list only learns of the replies it files through `report`, so a thread closed any
    other way — settled by another routine or by the operator, folded into a later thread,
    answered through a util — kept this assist asking a run for work it had already done.
    """
    owed = list(getattr(s.ctx, "reports_open", None) or [])
    if not owed:
        return False
    from .. import reports
    from ..report_threads import still_owed

    rows = reports.read_reports(reports.reports_path(s.ctx.server.routines_home))
    return bool(still_owed(rows, owed))


def _rendered_output_unseen(s: Situation) -> bool:
    """This run wrote something people LOOK at and never looked at it.

    interface-craft's moment. A page, a stylesheet, a figure or a typeset document is judged
    by how it renders; a run that only read its own source has judged none of it. The
    look is any `view_image`, or a call to a util that renders or sees a page; the
    deliverable is a file this run wrote or edited with a rendered extension.
    """
    records = getattr(s.loop, "turn_records", []) or []
    looked = any(r.get("kind") == "view_image"
                 or (r.get("kind") == "util" and str(r.get("brief") or "") in _LOOK_UTILS)
                 for r in records)
    if looked:
        return False
    return any(path.lower().endswith(_RENDERED) for path in _file_writes(s))


#: How many unanswered deferred asks make a run's ask policy worth surfacing. Low, because the
#: rule is about the FIRST reflex to defer rather than about a specific count.
_ASK_PILEUP = 3

#: The files a person judges by LOOKING at them rendered.
_RENDERED = (".html", ".htm", ".css", ".js", ".svg", ".pdf", ".tex")

#: The utils through which a run sees a rendered page or image.
_LOOK_UTILS = frozenset({"browser-session", "vision"})


#: name -> Predicate. The linter validates a rule's `predicate:` against these keys, so a
#: name removed here turns every rule that declares it into a lint error rather than a
#: silently dead assist.
PREDICATES: dict[str, Predicate] = {
    "repeated-failure": Predicate(
        moment="observation", check=_repeated_failure,
        describes="the same call has now failed twice this run"),
    "capability-denied": Predicate(
        moment="observation", check=_capability_denied,
        describes="a permission refused something this run wanted"),
    "user-corrected": Predicate(
        moment="boundary", check=_user_corrected,
        describes="the user just said something to this run"),
    "ledger-untouched": Predicate(
        moment="pre-finish", check=_ledger_untouched,
        describes="this run changed things and has not written to LEDGER.md"),
    "uncheckpointed-repo-write": Predicate(
        moment="pre-action", check=_uncheckpointed_repo_write,
        describes="this edits a git repo the engine does not version; its working tree "
                  "holds changes no commit can restore"),
    "asks-piling-up": Predicate(
        moment="boundary", check=_asks_piling_up,
        describes="several decisions are waiting on the user"),
    "unclosed-delivered-report": Predicate(
        moment="pre-finish", check=_unclosed_delivered_report,
        describes="another routine handed you work this run and nobody has answered it"),
    "rendered-output-unseen": Predicate(
        moment="pre-finish", check=_rendered_output_unseen,
        describes="this run wrote pages or documents and never looked at them rendered"),
}
