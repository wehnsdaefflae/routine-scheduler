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


class Uncertain(Exception):
    """The inference for a value's case context is too weak to assert — flag it, don't guess."""


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

    propose_changes(findings)                   # flagged items go out as DEFERRED decisions

    record(findings)
    return finish("ok", summary(findings))


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages/answers) and
    read the existing config map before re-discovering anything — so known locations aren't
    rediscovered from scratch. The digest already carries the LEDGER tail and says when there
    is more; read the file only if it says so."""
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


def propose_changes(findings):
    """File each flagged finding as a DEFERRED decision — the specific change, its source, and
    old→new — so the user settles it on the Decisions page and applies it themselves.

    This run does NOT change a live value. Changing one is an outward act needing the user's
    confirmation, and this pattern's holders are SCHEDULED routines with nobody watching: a
    blocking ask here waits out its timeout and settles nothing. The audit's deliverable is the
    report plus a decidable proposal per flagged value, not a silent edit."""
    for f in flagged(findings):
        ask_user(f.proposal, mode="deferred")   # → Decisions page; nothing is touched this run


def flagged(findings):
    """The sub-optimal / detrimental findings — the only candidates for a proposed change."""


def record(findings):
    """Update state/phase.json and the config map; append exactly one LEDGER entry: what was
    reviewed, how many values flagged, which proposals were filed — plus rejected candidates and
    why."""
    ledger.append("surfaces reviewed, flagged count, proposals filed, rejected + why")


def summary(findings):
    """One-line run summary: what was reviewed, how many values flagged, how many proposals are
    waiting on the user, and where the audit lives."""


if __name__ == "__main__":
    main()
