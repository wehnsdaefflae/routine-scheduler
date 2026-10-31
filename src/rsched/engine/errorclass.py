"""When a classifier cannot decide a provider failure STRUCTURALLY — the count, not the cure.

R3108, increment one, decided by the operator on 2026-10-17: *"Good idea! I want you to replace
the prose checks completely"*. Three places decide retry, failover and prompt-window policy by
matching ENGLISH SENTENCES in a provider's error body:

* `endpoints/base.py` — `transient_402 = resp.status_code == 402 and "in-flight requests" in
  resp.text` decides whether a 402 is retryable or fatal;
* `engine/overflow.py` — `_OVERFLOW_HINTS` (four phrases) minus `_NEVER_OVERFLOW_STATUS` (nine)
  decides whether to shrink the run's context window;
* `engine/degrade.py` — `_FAILOVER_REASONS` (five reasons, ~20 needles) decides the health
  stream's failure class.

Both failure modes are measured. On 2026-10-16 conversation `c-20261016-075101` died twice in
30 s logging `model_chain_exhausted … after 0 rungs` while the condition cleared itself. On
2026-09-21 the Anthropic overflow shape matched none of the openai-flavoured hints and
`self-audit:20260921-000321` 400'd four times with its prompt growing 1,017,305 → 1,045,385
while the engine failed over. `overflow.py`'s own comment states the mode: *"a hint list that
misses a vendor silently DISARMS this whole guard for it"*.

THIS MODULE CHANGES NO POLICY, deliberately. Nothing about which errors are retried, failed over
or shrunk moves; every existing decision comes out identical, which
`tests/test_error_class_undecided.py::test_no_existing_decision_changed` pins. What it adds is
the one number nobody has: how often a structured decision is actually UNAVAILABLE on this
instance. That number is what tells the next increment whether the prose lists were ever
load-bearing — replacing them on the strength of an argument, with no measurement of what they
catch, is how a guard gets removed from a vendor it was silently the only cover for. Removing
the prose matching is a LATER increment and must not start before this count exists.

WHAT THE ROWS SAY, and why each field is a field rather than prose (the F422 lesson — a question
answerable by a filter must not live in `detail`):

* `classifier` — `failover_reason` or `overflow_text`, the list that could not decide.
* `reason` / `declined_by` — the verdict it fell back to. On the overflow side the distinction
  is the whole reading: `no_hint_match` is the DANGEROUS bucket (a vendor shape no phrase
  covers, the 2026-09-21 mode), while `never_overflow_status` is the guard working as designed —
  a rate limit quoting two token counts, which must never be read as a context size or the run's
  window is pinned to a per-minute budget for every remaining turn.
* `error_keys_in_excerpt` — whether an OpenAI-compatible error object (`code` / `type` /
  `param`) was even PRESENT in the text the classifier saw. The structured data is sitting
  unparsed: `endpoints/base.py:259` renders a body to `resp.text[:300]` and `EndpointError`
  carries that string alone, so the decoded JSON reaches no decision point in the engine. A
  `false` here is the other finding — a response path where nothing structured is available at
  all (a proxy returning a bare body, which is why `0.420.1`'s `failure_hints.py` exists).

BEST-EFFORT BY CONSTRUCTION. Both notifiers sit on paths that are already handling a failure, so
every error here is swallowed and nothing is raised: a diagnostic that can turn a recovered
failure into a dead run is worse than no diagnostic. This is `engine/promptfloor.py`'s contract,
for the same reason.
"""

from __future__ import annotations

import re
from typing import Any

#: The event name. Registered in `health_events`' header enum in the same change — an event at a
#: call site and absent from that enum is invisible to every documented filter, which is the
#: `prompt_oversize_shrunk` failure `tests/test_health_events.py` exists to catch. It is an
#: INCIDENT, not a standing condition, so it is deliberately absent from
#: `health_cooldown.REPEAT_COOLDOWN_HOURS`: the count IS the deliverable, and a suppressed
#: second occurrence would understate exactly the number this event exists to produce.
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

    Called where the class is COMPUTED (`degrade._switch_to_fallback`) and not where it is
    logged: the 2026-10-16 specimen died as `model_chain_exhausted … after 0 rungs`, and that
    branch never reaches `_log_failover`, so hooking the log would have missed precisely the
    case that motivated this item. Returns None always; the failover decision is untouched.
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

    Called from `overflow._recover_oversize_prompt`'s "not ours" return, where both nets have
    already declined. A 400 that states a token figure and is still not read as a context fault
    is either a vendor shape the hint list misses or a correctly-excluded rate limit, and
    `declined_by` says which — without that split the count cannot be read. A 400 with no figure
    at all is an ordinary malformed-body rejection and files nothing: the subject is a classifier
    deciding without a structural basis, not every rejection. Returns None always.
    """
    try:
        text = str(exc)
        if _status(text) != 400:
            return
        quoted = _TOKENS_RE.search(text)
        if quoted is None:
            return
        from .overflow import _NEVER_OVERFLOW_STATUS, _is_overflow_text

        if _is_overflow_text(text):
            return
        low = text.lower()
        excluded = next((s for s in _NEVER_OVERFLOW_STATUS if s in low), "")
        _file(loop,
              f"a 400 quoting {quoted.group(0)} was not read as a context overflow"
              + (f" (excluded by '{excluded}')" if excluded
                 else " (no vendor hint matched its wording)")
              + f" — the window guard is unchanged and did not fire: {text[:200]}",
              classifier="overflow_text",
              declined_by="never_overflow_status" if excluded else "no_hint_match",
              status=400, excluded_by=excluded or None,
              error_keys_in_excerpt=bool(_ERROR_KEYS_RE.search(text)))
    except Exception:
        return
