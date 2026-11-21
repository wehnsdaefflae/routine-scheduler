"""BUILD SKEW — a long-lived run holding objects from the build it booted with, while the code
asking them questions was loaded from a newer build on disk (F677).

A module first imported mid-run is resolved FRESH FROM DISK, so a release that landed since boot
puts a NEW caller in front of an OLD held object (`GrantPolicy` above all); they disagree about
attribute names and it surfaces as a bare `AttributeError`.

Two defences, this module being the second:

1. close the window where possible — `finishstands.pin` resolves the finish path at run START;
2. ANSWER and NAME a mismatch that happens anyway: `setting_on` falls back from the held policy
   to the routine's own capabilities (the same reading `policyload` built the policy with), and
   the skew is recorded naming the held object, the attribute, and that a release landed.

Deliberately NOT a compatibility layer (no alias tables, no renamed-attribute map).
"""

from __future__ import annotations

from ..grants import SETTING_DEFAULTS, effective_settings, normalize_capabilities
from .run_context import RunContext

#: (run_id, attribute) pairs already recorded — one fact per run. Bounded; a clear only means
#: the same skew is recorded once more.
_NOTED: set[tuple[str, str]] = set()
_NOTED_CAP = 256


def describe(holder: object, name: str) -> str:
    """The DIAGNOSIS for one mismatch: the held object, the attribute, and the release that is
    the reason. No remedy clause — the caller adds that.
    """
    return (f"build mismatch (F677): the held {type(holder).__name__} object has no "
            f"`{name}` — this run booted with one build of the engine and the code asking "
            "for it was loaded from a newer one on disk, i.e. a release landed mid-run.")


def from_error(exc: AttributeError) -> str:
    """`describe` for an AttributeError that LOOKS like a build skew, "" when it does not.

    A missing attribute on a BUILTIN, or an error carrying no object, is an ordinary coding
    error, not a skew.
    """
    holder = getattr(exc, "obj", None)
    name = str(getattr(exc, "name", "") or "")
    if holder is None or not name or type(holder).__module__ == "builtins":
        return ""
    return describe(holder, name)


def note(ctx: RunContext, exc: AttributeError, *, did: str = "") -> str:
    """Record one skew on the run's own transcript — once per run per attribute — and return the
    sentence, or "" when the error is not a skew at all.

    Best-effort: an unwritable transcript must not turn a survivable mismatch into a crash.
    """
    if not (line := from_error(exc)):
        return ""
    message = f"{line} {did}".strip()
    key = (str(getattr(ctx, "run_id", "")), str(getattr(exc, "name", "")))
    if key in _NOTED:
        return message
    if len(_NOTED) >= _NOTED_CAP:
        _NOTED.clear()
    _NOTED.add(key)
    try:
        ctx.transcript.event("error", {"where": "build_skew", "build_skew": line,
                                       "message": message})
    except (OSError, AttributeError):
        pass
    return message


def setting_on(ctx: RunContext, key: str) -> bool:
    """Is the capability SETTING `key` (`jobs`, `reminders`, …) on for this run?

    Asked of the run's held policy first. When it cannot answer, read from the routine's OWN
    capabilities through `effective_settings` — the reading `policyload` built the policy with
    — where "on" means anything but the setting's all-off value, so the fallback cannot
    disagree with the policy.
    """
    try:
        return bool(getattr(ctx.grants, f"{key}_on"))
    except AttributeError as exc:
        note(ctx, exc, did="Answered from the routine's own capabilities instead.")
    caps, _problems = normalize_capabilities(ctx.routine.capabilities)
    off = SETTING_DEFAULTS.get(key, "off")
    return bool(effective_settings(caps).get(key, off) != off)
