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


def _failed_call(s: Situation) -> bool:
    """The observation that just returned is a failure.

    error-recovery's moment: the run has just been told something did not work, and the next
    action is where it either reads the failure or repeats it.
    """
    from .observations import is_failure

    return s.obs is not None and is_failure(s.obs)


def _user_corrected(s: Situation) -> bool:
    """A user message reached this run since the last boundary.

    intent-inference's moment: an intervention has just landed, and the question the rule
    asks — what standing preference does this imply? — is answerable now and stale later.
    `ctx.user_replies` counts the user's own utterances (a delivered report is a routine's
    message and does not count), so this is the arrival edge without new bookkeeping.
    """
    seen = getattr(s.loop, "assist_user_replies", 0)
    return int(getattr(s.ctx, "user_replies", 0) or 0) > int(seen or 0)


def _file_writes(s: Situation) -> list[str]:
    """The paths this run wrote or edited, from `turn_records`.

    `turn_records` is the run history that SURVIVES compaction — the message list does not,
    so a predicate that greps scrollback silently stops working on exactly the long runs that
    need it most. A `brief` is the action's identifying field, JSON-quoted.
    """
    return [str(r.get("brief") or "").strip('"')
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
    """
    from .harness import _is_conversation

    if not (s.ctx.routine.dir / "LEDGER.md").is_file():
        return False        # a routine that keeps no ledger is not being asked to start one
    if _is_conversation(s.ctx):
        return False
    wrote = _file_writes(s)
    if any("LEDGER.md" in path for path in wrote):
        return False        # the reasoning was recorded
    return any(not path.startswith("state/") for path in wrote)


def _uncheckpointed_repo_write(s: Situation) -> bool:
    """This action is about to edit a file inside a git repo the engine does not version.

    git-checkpoint's moment, and the one the design note reserves the HOLD rung for: the cost
    of skipping is irreversible in a way a reminder afterwards cannot undo. The engine
    autocommits the routine's OWN directory at run end, so that tree always has an undo point;
    a project repo the routine was granted a write root into has none unless the run makes one.

    Fires on the FIRST such write only — the one-fire-per-run rule is what makes "no checkpoint
    yet" true without having to detect a checkpoint commit, which happens inside a util or a
    shell command where the engine sees a command string and an exit code, nothing more.
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
    return any((parent / ".git").exists() for parent in [path, *path.parents])


def _asks_piling_up(s: Situation) -> bool:
    """This run has thrown several decisions over the wall without answers coming back.

    ask-policy's moment. `ctx.asks_deferred` is the engine's own churn telemetry — a deferred
    ask is a decision the run could not make and did not wait for — so a run accumulating them
    is the exact shape the rule is about: exhaust your own reach first, then defer a JUDGMENT,
    not a lookup.
    """
    return int(getattr(s.ctx, "asks_deferred", 0) or 0) >= _ASK_PILEUP


def _clean_claim_without_a_denominator(s: Situation) -> bool:
    """A finish summary reports all-clear without saying what was examined.

    unexamined-is-not-clean's moment. A review that found nothing is only meaningful beside
    the size of what it looked at, and the two readings — "I examined 40 files and found
    nothing" and "I looked at one and found nothing" — are the same sentence without it.
    Deliberately crude: it asks whether the summary claims cleanliness and carries no number
    at all, so a summary that quantifies ANYTHING passes. A predicate that tried to judge
    whether the denominator was the RIGHT one would be grading the reasoning again.
    """
    summary = str((s.action or {}).get("summary") or "").lower()
    if not any(claim in summary for claim in _CLEAN_CLAIMS):
        return False
    return not any(ch.isdigit() for ch in summary)


#: How many unanswered deferred asks make a run's ask policy worth surfacing. Low, because the
#: rule is about the FIRST reflex to defer rather than about a specific count.
_ASK_PILEUP = 3

#: The phrasings a run reaches for when it found nothing. Substrings on purpose — "no issues"
#: catches "no issues found" and "there were no issues".
_CLEAN_CLAIMS = ("all clear", "no issues", "no problems", "nothing to report", "clean bill",
                 "everything checks out", "no defects", "found nothing", "nothing wrong")


#: name -> Predicate. The linter validates a rule's `predicate:` against these keys, so a
#: name removed here turns every rule that declares it into a lint error rather than a
#: silently dead assist.
PREDICATES: dict[str, Predicate] = {
    "observation-failed": Predicate(
        moment="observation", check=_failed_call,
        describes="the call you just made failed"),
    "user-corrected": Predicate(
        moment="boundary", check=_user_corrected,
        describes="the user just said something to this run"),
    "ledger-untouched": Predicate(
        moment="pre-finish", check=_ledger_untouched,
        describes="this run has not written to LEDGER.md"),
    "uncheckpointed-repo-write": Predicate(
        moment="pre-action", check=_uncheckpointed_repo_write,
        describes="this edits a git repo the engine does not version, and no undo point "
                  "exists"),
    "asks-piling-up": Predicate(
        moment="boundary", check=_asks_piling_up,
        describes="several decisions are waiting on the user"),
    "clean-claim-without-a-denominator": Predicate(
        moment="pre-finish", check=_clean_claim_without_a_denominator,
        describes="the summary reports all-clear and names no number"),
}
