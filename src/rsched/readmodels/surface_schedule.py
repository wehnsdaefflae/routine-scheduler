"""The setup surface's SCHEDULE join — does this routine's file say when it actually runs,
and does the phase it records mean anything?

Both are NOTE rows: nothing is broken, the FILE is misleading. A member cron a lane's
schedule suppresses (D71) names a time the routine will never fire at; a routine in no
scheduled lane with no cron of its own is started by nothing on a clock; a `state/phase.json`
recording a key the composer does not read (`lifecycle`, `state`) scopes no stopping
condition to any stage. Split out of `surface.py` at 663 lines; the row vocabulary is
`surface_nodes`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .surface_nodes import NOTE, _node

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig

def _schedule_nodes(server: Any, cfg: RoutineConfig) -> list[dict]:
    """Does this routine's file say WHEN it actually runs?

    Two ways it can stop saying so, both silent. A LANE with a cron SUPPRESSES its members'
    own crons (D71), so a member that kept one has a file naming a time it will never fire at —
    `steward-hub-maintainer` recorded 23:00 while firing at 06:30 in its lane's chain, with
    nothing anywhere saying the two disagreed. The other way is the mirror: a routine with no
    cron of its own, in no scheduled lane, is never started by anything on a clock, which is a
    perfectly good on-demand design and indistinguishable from an oversight.

    Only the LANE is asked. A routine's domain shares a config block and a store but nothing on
    a clock; its tags fire nothing at all. Nothing on either axis can make this file disagree
    with itself (docs/lanes-domains.md).

    Neither breaks a run, so neither shouts. What they cost is the operator's belief about when
    the routine runs, which is exactly what a NOTE is for.
    """
    from .. import lanes as lanes_mod

    if not cfg.enabled:
        return []                       # a disabled routine already says it does not run
    from ..engine.stopping import goal_reached
    if goal_reached(cfg.dir):
        # Not a misconfiguration — the opposite. Every goal-scoped stopping condition is met, so
        # the scheduler stops firing it. Said out loud because the page would otherwise show a
        # cron that will never fire again with nothing explaining why.
        #
        # And said with NO fix, because nothing here is unmet: the row reports a success; an
        # offer to reopen the goal is an affordance for undoing one. Reopening is a decision a
        # person makes about the work, taken in the panel that owns the conditions (`_node`).
        return [_node("schedule:goal", "retired", NOTE,
                      "every final-goal stopping condition is met, so this routine is FINISHED "
                      "and nothing fires it any more",
                      "its schedule is inert and a lane chain skips it; the Decisions page "
                      "carries the proposal that retires it for good")]
    try:
        all_lanes = lanes_mod.list_lanes(server.routines_home)
    except OSError:
        return []
    # At most one can match — lane membership is exclusive (rsched.lanes) — but the scan is
    # over the store rather than a per-routine key, so it reads as a list either way.
    mine = [lane for lane in all_lanes if cfg.slug in lanes_mod.member_slugs(lane)]
    scheduling = next((lane for lane in mine if lane.get("cron")), None)
    if scheduling and cfg.cron:
        paused = " (currently PAUSED, so nothing fires at all)" if scheduling.get("paused") else ""
        # `source` carries the lane's id because the prose can only name it — a name is not
        # addressable and the operator's next step is that lane's row. surface-view.js groups
        # rows by `doc` / `utils` alone, so this one appears under "from this routine's own
        # config"; the FIX carries the same lane, which is what a link can be built from.
        return [_node("schedule:cron", "suppressed", NOTE,
                      f"its lane {scheduling['name']!r} carries a schedule, which suppresses "
                      "every member's own cron",
                      f"this file records {cfg.cron!r} while the routine actually fires with "
                      f"the lane at {scheduling['cron']!r}{paused}",
                      {"kind": "lane_schedule", "lane": scheduling["id"],
                       "name": scheduling["name"]}, {"lane": scheduling["id"]})]
    if not scheduling and not cfg.cron:
        how = ("a trigger, a hand fire, or another run's schedule_run" if cfg.triggers
               else "a hand fire from the console, or another run's schedule_run")
        return [_node("schedule:none", "unscheduled", NOTE,
                      "it has no cron of its own and no lane schedules it",
                      f"nothing on a clock starts this routine; it runs only on {how}",
                      {"kind": "set_schedule"})]
    return []


def _phase_nodes(server: Any, cfg: RoutineConfig) -> list[dict]:
    """Does `state/phase.json` record the phase under the key the engine reads?

    The composer reads it as `.get("phase")`; that value is what scopes a stopping
    condition to a stage. Routines that invented their own key wrote a file that LOOKS right
    and matches nothing: funscript-trainer recorded `lifecycle`, self-audit `state`,
    routine-improver an empty object. Nothing breaks — the digest dumps the whole object, so
    the run still reads it — but every stage-scoped condition silently never fires.

    Only said for a routine whose recipe actually TRACKS a phase — detected by the recipe
    naming `state/phase.json`, not by a `## Phases` heading: the routines that get this wrong
    are exactly the ones that describe their phase in prose (self-audit walks a state machine
    through it, routine-improver a step cursor) and have no such heading. A routine that never
    mentions the file is not missing anything.

    ABSENCE is only a gap once a run has COMPLETED without recording one —
    the composer reads a missing file as "likely the first run"; the run in flight right
    now has not had its chance yet. A CONVERSATION is skipped outright: the converse pattern
    declares a phase, but conversations.py never writes one to state/phase.json by design, so
    every reply would carry a boot note about a file that is correctly absent.
    """
    from ..paths import read_json

    routine_dir = Path(getattr(cfg, "dir", None) or server.routines_home / cfg.slug)
    conversations = getattr(server, "conversations_home", None)
    if conversations is not None:
        try:
            if routine_dir.resolve().parent == Path(conversations).resolve():
                return []
        except OSError:
            return []
    main = routine_dir / "main.md"
    if not main.is_file():
        return []
    recipe = main.read_text(encoding="utf-8", errors="replace")
    for stage in sorted((routine_dir / "stages").glob("*.md")):
        recipe += stage.read_text(encoding="utf-8", errors="replace")
    if "phase.json" not in recipe and "## Phases" not in recipe:
        return []
    raw = read_json(routine_dir / "state" / "phase.json")
    if raw is None:
        if not any((routine_dir / "runs").glob("*/result.md")):
            return []
        return [_node("state:phase", "absent", NOTE,
                      "its recipe declares phases but no completed run has recorded one",
                      "the digest reports no phase and any stage-scoped stopping condition "
                      "never matches; the next run that records a phase fixes it",
                      {"kind": "fix_phase", "expected": "phase"})]
    if not isinstance(raw, dict) or not str(raw.get("phase") or "").strip():
        found = ", ".join(sorted(raw)) if isinstance(raw, dict) and raw else "nothing"
        return [_node("state:phase", "mis-keyed", NOTE,
                      "state/phase.json does not record the phase under the `phase` key",
                      f"the engine reads `phase`; this file holds {found}. The digest still "
                      "shows the object, but stage-scoped stopping conditions never match",
                      {"kind": "fix_phase", "expected": "phase"})]
    return []
