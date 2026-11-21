"""When a classifier cannot decide a provider failure STRUCTURALLY — the count, not the cure.

R3108. Three places decide retry, failover and prompt-window policy by matching ENGLISH
SENTENCES in a provider's error body (`endpoints/base.py`'s `transient_402`,
`engine/overflow.py`'s overflow hints, `engine/degrade.py`'s `_FAILOVER_REASONS`), and a hint
list that misses a vendor silently disarms its guard for it.

THIS MODULE CHANGES NO POLICY, deliberately: every existing decision comes out identical
(`tests/test_error_class_undecided.py::test_no_existing_decision_changed`). It adds the one
number that says how often a structured decision is UNAVAILABLE — which must exist before the
prose matching is removed.

The rows' fields (filterable, not prose in `detail`):

* `classifier` — `failover_reason` or `overflow_text`, the list that could not decide.
* `reason` / `declined_by` — the verdict it fell back to. On the overflow side `no_hint_match`
  is the DANGEROUS bucket (a vendor shape nothing covers), while `never_overflow_status` is the
  guard working — e.g. a rate limit quoting token counts, never a context size.
* `error_keys_in_excerpt` — whether an OpenAI-compatible error object (`code` / `type` /
  `param`) was PRESENT in the text the classifier saw; `false` means nothing structured was
  available at all.

BEST-EFFORT BY CONSTRUCTION: both notifiers sit on paths already handling a failure, so every
error here is swallowed — a diagnostic must never turn a recovered failure into a dead run.
"""

from __future__ import annotations

import re
from typing import Any

from ..endpoints import errorsignal

#: The event name (registered in `health_vocab.EVENTS`). An INCIDENT, deliberately absent from
#: `health_cooldown.REPEAT_COOLDOWN_HOURS`: the count IS the deliverable.
EVENT = "error_class_undecided"

_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")
#: A token figure quoted in the body. Three digits up, so a passing "0 tokens" is not a count.
_TOKENS_RE = re.compile(r"(\d{3,9})\s*tokens?\b", re.IGNORECASE)
#: The keys an OpenAI-compatible error object carries (`error.code` / `error.type` /
#: `error.param`). Matched TEXTUALLY on the excerpt, because that is all the classifier had.
_ERROR_KEYS_RE = re.compile(r'"(?:code|type|param)"\s*:', re.IGNORECASE)


def _status(text: str) -> int | None:
    """The HTTP status the error text names, or None — every endpoint renders it as `HTTP nnn`."""
    found = _STATUS_RE.search(text)
    return int(found.group(1)) if found else None


def _file(loop: Any, detail: str, **fields: Any) -> None:
    """Append one row for the run that saw it. Raises only to the notifiers' own guard."""
    from ..health_events import log_health_event

    ctx = loop.ctx
    log_health_event(ctx.server.routines_home, EVENT,
                     routine=getattr(ctx.routine, "slug", "") or "",
                     run_id=getattr(ctx, "run_id", "") or "",
                     detail=detail, **fields)


def note_failover_class(loop: Any, exc: Any, reason: str) -> None:
    """File the event when a hard failure's class could not be decided — `reason` is `other`.

    Called where the class is COMPUTED (`degrade._switch_to_fallback`), not where it is logged:
    the chain-exhausted branch never reaches `_log_failover`. The failover decision is untouched.
    """
    try:
        if reason != "other":
            return
        text = str(exc)
        _file(loop,
              f"a hard failure's class could not be decided from its text, so the honest "
              f"`other` bucket was recorded — retry and failover are unchanged: {text[:200]}",
              classifier="failover_reason", reason=reason, status=_status(text),
              error_keys_in_excerpt=bool(_ERROR_KEYS_RE.search(text)))
    except Exception:
        return


def note_overflow_declined(loop: Any, exc: Any) -> None:
    """File the event when the overflow classifier DECLINED a 400 whose body quotes token counts.

    Called from `overflow._recover_oversize_prompt`'s "not ours" return. Such a 400 is either a
    vendor shape nothing covers or a correctly-excluded rate limit, and `declined_by` says which.
    A 400 with no token figure is an ordinary rejection and files nothing.
    """
    try:
        text = str(exc)
        if _status(text) != 400:
            return
        quoted = _TOKENS_RE.search(text)
        if quoted is None:
            return
        # the basis is the DECLARED signal (endpoints/errorsignal); `declined_by` may also
        # read `no_structured_error`
        signal = errorsignal.of(exc)
        if errorsignal.is_overflow(signal):
            return
        why = errorsignal.overflow_decline_reason(signal)
        excluded = next(iter(signal.declared), "") if why == "never_overflow_status" else ""
        _file(loop,
              f"a 400 quoting {quoted.group(0)} was not read as a context overflow"
              + (f" (excluded by '{excluded}')" if excluded
                 else " (it declared nothing a context fault could be read from)")
              + f" — the window guard did not fire: {text[:200]}",
              classifier="overflow_text", declined_by=why,
              status=400, excluded_by=excluded or None,
              error_keys_in_excerpt=bool(_ERROR_KEYS_RE.search(text)))
    except Exception:
        return
