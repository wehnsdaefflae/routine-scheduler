"""The predicate registry — the deterministic checks a rule's `assists:` block names.

A library document declares a check by NAME; the implementation lives here, because a rule is
prose in a git-synced multi-writer directory and must never be able to ship code.

Every predicate is a pure question about the SITUATION — the action just emitted, the
observation just returned, what this run has done so far — never about the model's reasoning,
which is not in the trace. Each declares the MOMENT it answers at, so the linter can refuse a
rule that asks a pre-finish question at an observation.

Precision is the whole budget: a line surfaced at the wrong moment is worse than none. Prefer
a predicate that misses to one that guesses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .assist_child import child_fell_short, spawned_a_thin_brief
from .assist_outcome import outcome_questioned
from .assist_restart import work_cut_by_daemon_exit
from .assist_runend import children_killed_at_end


@dataclass(frozen=True)
class Situation:
    """Everything a predicate may look at. Fields absent at a moment are None."""

    loop: Any
    action: dict | None = None      # the action just emitted (observation, pre-finish)
    obs: dict | None = None         # the observation just returned (observation)
    child: dict | None = None       # the child run that just exited (child-exit)
    ending: dict | None = None      # how the run closed out, as recorded (run-end)
    restart: dict | None = None      # the daemon exit cutting this run's turn (restart)

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

    fix-the-cause's moment: the SECOND failure of the same call is where a run either changes
    route or starts engineering around a wall. `loop.failures` is tallied through the same
    `exitgate.failure_tally_key` seam this asks, so the two can never disagree about whether a
    call failed (a DECLARED informational exit is not a failure on either side).
    """
    from .exitgate import failure_tally_key

    if s.obs is None or s.action is None:
        return False
    key = failure_tally_key(s.ctx, s.action, s.obs)
    return bool(key) and int((getattr(s.loop, "failures", None) or {}).get(key, 0)) == 2


def _capability_denied(s: Situation) -> bool:
    """A permission refused this run something it wanted; the run moved on without
    asking for it.

    ask-policy's moment. A refusal happens at the validation seam (`ctx.last_denial_turn`) or
    comes back as the observation (an fs or secret gate), the latter carrying the ONE wording
    `GrantPolicy.request_route` renders. A run whose next action files the request is left
    alone.
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

    fix-the-cause's moment: what standing preference a correction implies is answerable now
    and stale later. `ctx.user_replies` counts only the user's own utterances.
    """
    seen = getattr(s.loop, "assist_user_replies", 0)
    return int(getattr(s.ctx, "user_replies", 0) or 0) > int(seen or 0)


def _file_writes(s: Situation) -> list[str]:
    """The paths this run wrote or edited, from `turn_records`.

    `turn_records` SURVIVES compaction (the message list does not); a `brief` is the action's
    identifying field, whole (`compaction.turn_record`).
    """
    return [str(r.get("brief") or "")
            for r in getattr(s.loop, "turn_records", []) or []
            if r.get("kind") in ("write_file", "edit_file")]


def _uncheckpointed_repo_write(s: Situation) -> bool:
    """This action is about to edit a file inside a git repo the engine does not version.

    git-checkpoint's moment, a HOLD because skipping is irreversible. The engine autocommits
    the routine's OWN directory at run end; a granted project repo has no undo point unless
    the run makes one. Fires on the FIRST such write only (one fire per run), so no checkpoint
    commit has to be detected.

    A repo found CLEAN is an undo point for the rest of the run, remembered in
    `loop.assist_undo_points` and never asked about again. The finding is named on the
    write's observation (`assist.recorded`), from which a resumed leg rebuilds the set
    (engine/guardscope.py).
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
    point. A tree git cannot read is treated as dirty: this hold must not miss. Asks through
    `libgit.git`, whose reads never take the index lock (these are the operator's repos).
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

    ask-policy's moment: `ctx.asks_deferred` counts decisions the run could not make and did
    not wait for.
    """
    return int(getattr(s.ctx, "asks_deferred", 0) or 0) >= _ASK_PILEUP


def _unclosed_delivered_report(s: Situation) -> bool:
    """This run was handed work by another routine and is ending without answering it.

    problem-routing's receiving half (D131): closing the thread is part of shipping the fix,
    and the finish is the last moment the run that did the work can do it.

    `ctx.reports_open` is the engine's own bookkeeping (the drain adds, an `answers` reply
    removes; a `closes: true` closure never enters). What is still owed is re-read from the
    folded reports at the moment of asking (R2086), since a thread can be closed other ways.
    """
    owed = list(getattr(s.ctx, "reports_open", None) or [])
    if not owed:
        return False
    from .. import reports
    from ..report_threads import still_owed

    home = s.ctx.server.routines_home
    # `read_reports` folds the HOOK STREAM (D152 step 9) — the one source every report
    # surface reads, so the hold is placed on the count a person can see.
    return bool(still_owed(reports.read_reports(reports.reports_path(home)), owed))


def _rendered_output_unseen(s: Situation) -> bool:
    """This run wrote something people LOOK at and never looked at it.

    interface-craft's moment. The look is any `view_image` or a call to a util that renders
    or sees a page; the deliverable is a written/edited file with a rendered extension.
    """
    records = getattr(s.loop, "turn_records", []) or []
    looked = any(r.get("kind") == "view_image"
                 or (r.get("kind") == "util" and str(r.get("brief") or "") in _LOOK_UTILS)
                 for r in records)
    if looked:
        return False
    return any(path.lower().endswith(_RENDERED) for path in _file_writes(s))


#: Unanswered deferred asks that make a run's ask policy worth surfacing; low on purpose.
_ASK_PILEUP = 3
#: Files a person judges by LOOKING at (or WATCHING) them rendered.
_RENDERED = (".html", ".htm", ".css", ".js", ".svg", ".pdf", ".tex",
             ".mp4", ".webm", ".mov", ".gif", ".apng", ".webp")
#: The utils through which a run sees a rendered page or image — or watches a moving one.
_LOOK_UTILS = frozenset({"browser-session", "vision", "video-extract"})


#: name -> Predicate. The linter validates a rule's `predicate:` against these keys, so a
#: removed name is a lint error rather than a silently dead assist.
PREDICATES: dict[str, Predicate] = {
    "repeated-failure": Predicate(moment="observation", check=_repeated_failure,
        describes="the same call has now failed twice this run"),
    "capability-denied": Predicate(moment="observation", check=_capability_denied,
        describes="a permission refused something this run wanted"),
    "user-corrected": Predicate(moment="boundary", check=_user_corrected,
        describes="the user just said something to this run"),
    "uncheckpointed-repo-write": Predicate(moment="pre-action", check=_uncheckpointed_repo_write,
        describes="this edits a git repo the engine does not version; its working tree "
                  "holds changes no commit can restore"),
    "asks-piling-up": Predicate(moment="boundary", check=_asks_piling_up,
        describes="several decisions are waiting on the user"),
    "unclosed-delivered-report": Predicate(moment="pre-finish",
        check=_unclosed_delivered_report,
        describes="another routine handed you work this run and nobody has answered it"),
    "rendered-output-unseen": Predicate(moment="pre-finish", check=_rendered_output_unseen,
        describes="this run wrote pages or documents and never looked at them rendered"),
    "outcome-questioned": Predicate(moment="boundary", check=outcome_questioned,
        describes="the person's words question what this routine PRODUCES"),
    "child-fell-short": Predicate(moment="child-exit", check=child_fell_short,
        describes="a child run you started just exited without finishing ok"),
    "thin-child-brief": Predicate(moment="spawn", check=spawned_a_thin_brief,
        describes="you just started a child on a brief that may not stand on its own"),
    "children-killed-at-end": Predicate(moment="run-end", check=children_killed_at_end,
        describes="children were still working when this run ended, so they were terminated"),
    "work-cut-by-daemon-exit": Predicate(moment="restart", check=work_cut_by_daemon_exit,
        describes="the daemon went down while this run was mid-work, cutting its turn"),
}
