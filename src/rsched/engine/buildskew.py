"""BUILD SKEW — a long-lived run holding objects from the build it booted with, while the code
asking them questions was loaded from a newer build on disk.

F677. A run of this instance's own repository ships releases while it is running: one routine's
whole job, twice a day. The engine process holds the modules it imported at boot, and the
OBJECTS those modules made — `GrantPolicy` above all, built once in `loopsetup` and read by every
gate for the rest of the run. A module not yet imported at boot is resolved FRESH FROM DISK the
moment something imports it, so a release that landed in between puts a NEW caller in front of an
OLD held object. The two then disagree about attribute names, and the disagreement surfaces as a
bare `AttributeError` on whichever turn first needs that module.

It cost three runs their finish gate on 2026-10-12 (07:34-08:26: scheduler-builder,
llmsectest-weekday, global-utils-review — the instance's first three `finish_gate_failed`
events). 0.416.0, committed 07:23, renamed `taskops.py` to `jobops.py` and `tasks_on` to
`jobs_on`; the finish path is reached through one deliberately lazy import
(`engine/finishstands.py`), so each of those runs loaded the new `jobops` at its finish turn and
asked a policy object from the old build for `jobs_on`. It failed OPEN — all three runs finished
— and what was lost was the judging: a jobs gate that silently did not run.

TWO THINGS ARE NEEDED, and this module is the second:

1. the window is closed where it can be — `finishstands.pin` resolves the finish path at run
   START, while the build on disk is still the build this process booted with;
2. a mismatch that happens anyway is ANSWERED and NAMED. A capability SETTING is config, not
   state: `setting_on` asks the held policy first and falls back to the routine's own
   capabilities — the same reading `policyload` built the policy with — so the gate keeps its
   real verdict instead of crashing or defaulting to off. And the skew goes on the run's record
   in words that name the mismatch: WHICH held object, WHICH attribute, and that a release
   landed mid-run. "AttributeError: 'GrantPolicy' object has no attribute 'jobs_on'" reads as an
   engine bug; those three rows sent nobody to the release that caused them.

Deliberately NOT a compatibility layer (no alias tables, no renamed-attribute map): nothing here
teaches the engine to speak an older build's vocabulary. It answers one question from config and
reports the skew.
"""

from __future__ import annotations

from ..grants import SETTING_DEFAULTS, effective_settings, normalize_capabilities
from .run_context import RunContext

#: (run_id, attribute) pairs already recorded — one skew is one fact per run, and a setting is
#: asked on nearly every turn. Bounded: a process serves one run tree, and a cleared set only
#: means a later turn records the same skew once more.
_NOTED: set[tuple[str, str]] = set()
_NOTED_CAP = 256


def describe(holder: object, name: str) -> str:
    """The DIAGNOSIS for one mismatch, in the terms a reader can act on: the held object, the
    attribute it was asked for, and the release that is the reason. No remedy clause — what was
    done about it differs per call site, and the caller adds that.
    """
    return (f"build mismatch (F677): the held {type(holder).__name__} object has no "
            f"`{name}` — this run booted with one build of the engine and the code asking "
            "for it was loaded from a newer one on disk, i.e. a release landed mid-run.")


def from_error(exc: AttributeError) -> str:
    """`describe` for an AttributeError that LOOKS like a build skew, "" when it does not.

    Python records the object and the attribute on the exception, which is what makes the
    reading possible at all. A missing attribute on a BUILTIN container is an ordinary coding
    error — dressing one up as a release would make the sentence worthless — and so is an error
    carrying no object at all.
    """
    holder = getattr(exc, "obj", None)
    name = str(getattr(exc, "name", "") or "")
    if holder is None or not name or type(holder).__module__ == "builtins":
        return ""
    return describe(holder, name)


def note(ctx: RunContext, exc: AttributeError, *, did: str = "") -> str:
    """Record one skew on the run's own transcript — once per run per attribute — and return the
    sentence, or "" when the error is not a skew at all.

    Best-effort like every telemetry write on these seams: a transcript that cannot be written
    must not turn a survivable mismatch back into a crash.
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

    Asked of the run's held policy first — that object is the one place that decides what a run
    may do, and nothing here second-guesses it. When the held object cannot answer, the setting
    is read from the routine's OWN capabilities instead: `effective_settings` is the reading
    `policyload` built the policy with and the settings page shows, and "on" means anything but
    the setting's all-off value, exactly as the policy's own properties define it. So the
    fallback cannot disagree with the policy about what the routine holds. DATA survives a
    release; an object held across one does not.
    """
    try:
        return bool(getattr(ctx.grants, f"{key}_on"))
    except AttributeError as exc:
        note(ctx, exc, did="Answered from the routine's own capabilities instead.")
    caps, _problems = normalize_capabilities(ctx.routine.capabilities)
    off = SETTING_DEFAULTS.get(key, "off")
    return bool(effective_settings(caps).get(key, off) != off)
