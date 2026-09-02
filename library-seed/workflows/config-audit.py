"""Config audit — discover, infer case, evaluate every value, report, gated-apply.

An audit / survey-and-report pattern for a known-but-not-pre-located set of config surfaces.
Each run is a FULL re-audit (not incremental): (1) DISCOVER where each surface's user-editable
values live and record those locations in a reusable state map; (2) INFER and record the
deployment's case context by inspecting the scheduler and its routines; (3) EVALUATE every value
in each surface against ideal-value criteria, flagging detrimental/sub-optimal ones with both a
generally-sound default and a case-specific recommendation; (4) COMPILE a structured report at
the audit path; (5) an OPTIONAL per-item gated branch that, only after per-item user
confirmation, applies an accepted change at its source and logs it.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*, one
engine action per turn, following the control flow below. The dummy imports name the parameters
the clarifier fixes for the concrete task; `decompose` turns this pattern into the routine's own
markdown state-machine.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    CONFIG_SURFACES,    # list[str] — the config surfaces to cover every run (e.g. global scheduler,
                        #             per-routine, defaults, main project); enumerated, not pre-located
    AUDIT_PATH,         # str       — where the markdown audit is written/refreshed (e.g. state/config-audit.md)
    CONFIG_MAP_PATH,    # str       — the state map of where each surface lives (e.g. state/config-map.json)
    IDEAL_CRITERIA,     # str       — what 'detrimental / sub-optimal / ideal' mean for this deployment
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Config audit",
    "slug": "config-audit",
    "description": "Discover config surfaces, infer the deployment's case, evaluate every value, "
                   "report flagged items with default + case-specific fixes, apply only confirmed changes.",
    "when_to_use": "Recurring instructions that survey a known-but-not-pre-located set of "
                   "configuration surfaces, judge each value against ideal-value criteria, and "
                   "compile a recommendations report — with optional per-item gated application of "
                   "accepted changes. Use it when the deliverable is an audit whose locations must "
                   "be discovered once and remembered, and each run is a full re-audit.",
    "version": 1,
    "tags": ["audit", "config", "review", "recommendations", "survey"],
    "includes": ["ask-policy", "decision-record"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["discover", "steady"]     # discover: first-run location map; steady: full re-audit each run
COMPLETION = (
    "per run: every surface reviewed, a verdict + (where flagged) default and case-specific "
    "recommendation emitted for each value examined, any confirmed changes applied and logged, "
    "AUDIT_PATH written; overall: open-ended — a fresh full audit each scheduled run"
)


class Uncertain(Exception):
    """The inference for a value's case context is too weak to assert — flag it, don't guess."""


class Unconfirmed(Exception):
    """A proposed change lacked per-item confirmation — leave it pending in the report."""


def main():
    """One run of the routine — a complete re-audit of all surfaces, then an optional gated apply."""
    orient()                                    # consume state digest + LEDGER + the location map

    surface_map = load_or_discover()            # where each surface's live values actually live
    case = infer_case(surface_map)              # the deployment's evident priorities/constraints

    findings = []
    for surface in CONFIG_SURFACES:             # all surfaces every run — never skip one
        values = read_surface(surface_map, surface)   # read live values directly, never from memory
        for value in values:
            findings.append(evaluate(value, case))    # verdict + default/case-specific recommendation

    write_report(findings, case, surface_map)   # COMPILE the structured audit, flagged items first

    applied = apply_confirmed(findings, surface_map)  # OPTIONAL gated branch — per-item confirmation only

    record(findings, applied)
    return finish("ok", summary(findings, applied))


def orient():
    """Read the state digest (phase, last result, LEDGER tail, user messages/answers) and the
    existing config map before re-discovering anything — so known locations aren't rediscovered
    from scratch."""
    read_file("LEDGER.md")
    read_file(CONFIG_MAP_PATH)


def load_or_discover():
    """Read CONFIG_MAP_PATH first. For any surface it already records, reuse the location and only
    confirm it still resolves; for surfaces that are missing, moved, or new, DISCOVER where the
    user-editable values live — config files in the project tree, per-routine config files, a
    settings export, or an API/util — by inspecting the deployment (read_file / util). Record each
    surface's location AND what it holds back to the map. On the discover phase this builds the map
    from nothing; advance phase to 'steady' once every surface is located."""


def infer_case(surface_map):
    """INFER the deployment's context by inspecting the scheduler setup and existing routines —
    their number, cadence, resource usage, dependencies, evident priorities (reliability vs. cost
    vs. speed) and constraints (rate limits, budget, single-machine). Use `llm` for a scoped
    judgment over what was read. Return a structured case record; mark any dimension whose evidence
    is weak so evaluate() can flag it rather than assert. Record this alongside the report."""


def read_surface(surface_map, surface):
    """Read the live user-editable values for one surface directly from the source the map names —
    file, export, or util. Never guess a value that has not been read; if the source no longer
    resolves, route back through load_or_discover for this surface."""


def evaluate(value, case):
    """Judge one value against IDEAL_CRITERIA and the inferred case. Emit a verdict — ok /
    sub-optimal / detrimental — where detrimental risks correctness, reliability, data loss, or
    runaway resource/cost, and sub-optimal means a safer/more-efficient value serves better with no
    downside. Where flagged, give BOTH recommendations when they differ: the generally-sound
    default and the case-specific value. Each rationale (one line) cites the criterion and the case
    context used."""
    try:
        return judge(value, case)
    except Uncertain:
        # Case inference too weak to assert — recommend the default, flag the case gap explicitly.
        return judge_default_only(value)


def judge(value, case):
    """Return the full finding (current, verdict, default rec, case-specific rec, rationale) for a
    value where the case context is solid enough to cite."""


def judge_default_only(value):
    """Return a finding with the generally-sound default only, explicitly noting the case-specific
    recommendation is withheld because the inference is uncertain."""


def write_report(findings, case, surface_map):
    """COMPILE / refresh the markdown audit at AUDIT_PATH: flagged items grouped up front, then a
    section per surface listing every value examined — current value, verdict, recommended
    (default and/or case-specific), one-line rationale. Include the inferred case and where each
    surface lives so later runs reuse it. This is written before any change is applied."""
    write_file(AUDIT_PATH, "flagged-first audit: per-surface value/verdict/recommendation/rationale")


def apply_confirmed(findings, surface_map):
    """OPTIONAL gated branch. Changing a live value is an outward act: for each flagged
    recommendation, PROPOSE the specific change and get the user's per-item confirmation
    (ask-policy). Only on an explicit yes, edit that one value at its source; never batch-apply and
    never apply an unconfirmed change. Return the list of applied (surface, key, old→new)."""
    applied = []
    for f in flagged(findings):
        try:
            answer = ask_user(f.proposal, mode="blocking")   # per-item, before touching the source
            if not accepted(answer):
                raise Unconfirmed
            edit_value_at_source(surface_map, f)             # edit exactly this value, at its source
            applied.append(f)
        except Unconfirmed:
            leave_pending(f)                                 # stays in the report as a pending rec
            continue
    return applied


def flagged(findings):
    """The sub-optimal / detrimental findings — the only candidates for a proposed change."""


def edit_value_at_source(surface_map, finding):
    """Write the accepted value back to the exact source the map names (file/export/util), touching
    only that one key. Read it back to confirm the edit landed."""


def leave_pending(finding):
    """Record an accepted-in-report-but-unconfirmed recommendation as pending (e.g. when
    confirmation isn't available) so it isn't lost and isn't silently applied."""


def record(findings, applied):
    """Update state/phase.json and the config map; append exactly one LEDGER entry: what was
    reviewed, how many values flagged, every applied change (surface, key, old→new), and any
    pending items — plus rejected candidates and why."""
    ledger.append("surfaces reviewed, flagged count, applied changes old→new, pending, rejected")


def summary(findings, applied):
    """One-line run summary: what was reviewed, how many values flagged, how many changes applied,
    and where the audit lives."""


if __name__ == "__main__":
    main()
