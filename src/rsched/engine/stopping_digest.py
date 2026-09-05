"""The STOPPING CONDITIONS block the composer inlines — the prose half of `stopping.py`.

Split out for the reason `harness.py` was split out of `composer.py` (F393): keeping the store,
the evaluation and the accounting parser in one file is one job, and writing the paragraph the
model obeys is another. Everything here is prompt text; `docs/prompt-anatomy.md` pins the wording
and `tests/test_prompt_anatomy.py` fails on drift.

The block renders the two SCOPES apart, because they ask the model two different questions:

- **RUN BOUNDS** (`scope: "run"`) — what THIS run must achieve and where it must stop. Re-asked
  every run, never carried: a per-run bound cannot be "already met", and treating it as met is
  what left 22 of 31 live routines reading "the job is DONE. Finish NOW" at the top of every run
  before this split existed.
- **FINAL GOAL** (`scope: "goal"`) — the state after which the ROUTINE itself is finished. Sticky:
  once met it stays met, and the daemon stops firing the routine (see `engine/goalreached.py`).
  A run that cannot reach it says how far away it is, which is the pressure the goal exists to
  apply — a goal reported `unmet — 3 of 5 documents drafted, blocked on the letter of support`
  every run is the routine telling you where it stands, not noise.
"""

from __future__ import annotations

from pathlib import Path

from . import stopping


def _line(cond: dict, by_id: dict, phase: str) -> str:
    mark = {"met": "✓", "dropped": "–"}.get(cond["status"], "○")
    why = stopping.blocked_reason(cond, by_id, phase=phase) if cond["status"] == "open" else ""
    bits = [why] if why else []
    # A run bound never carries a status, so the ONLY thing that can orient the next run is what
    # the last one concluded. Rendered as history ("last run: …"), never as a current verdict.
    if cond["scope"] == "run" and cond.get("last_verdict"):
        note = f" — {cond['note']}" if cond.get("note") else ""
        bits.append(f"last run: {cond['last_verdict']}{note}")
    tail = f"  ({'; '.join(bits)})" if bits else ""
    return f"  {mark} [{cond['id']}] {cond['text']}{tail}"


def _group_block(doc: dict, live: list[dict], by_id: dict, phase: str) -> list[str]:
    out: list[str] = []
    for g in doc["groups"]:
        members = [c for c in live if c["group"] == g["id"]]
        if not members:
            continue
        label = f'"{g["name"]}" ' if g["name"] else ""
        out.append(f"{label}— {g['mode'].upper()} of:")
        out.extend(_line(c, by_id, phase) for c in members)
    return out


def digest_section(routine_dir: Path, *, phase: str = "") -> str:
    """The always-visible prompt block (state_digest inlines it beside the plan).

    Renders the STRUCTURE, not a flat list: a run that cannot see that two conditions are an OR
    will treat them as an AND and work past the point the user meant it to stop.
    """
    doc = stopping.load(routine_dir)
    live = [c for c in doc["conditions"] if c["status"] != "dropped"]
    if not live:
        return ""
    by_id = {c["id"]: c for c in doc["conditions"]}
    joiner = "ALL of these groups" if doc["mode"] == "all" else "ANY of these groups"
    out: list[str] = []

    run_live = [c for c in live if c["scope"] == "run"]
    if run_live:
        out.append("STOPPING CONDITIONS (state/stopping.json — the USER's meaning-level bounds on "
                   "THIS RUN; the engine cannot judge them, you must). This run is done when "
                   f"{joiner} is satisfied:")
        out += _group_block(doc, run_live, by_id, phase)

    goal_live = [c for c in live if c["scope"] == "goal"]
    if goal_live:
        out.append(("\n" if run_live else "")
                   + "FINAL GOAL (state/stopping.json — the state after which this ROUTINE is "
                     "finished and stops running altogether). It is not this run's target unless "
                     "this run can actually reach it; what it asks of you every run is an honest "
                     f"reading of the DISTANCE remaining. The routine is finished when {joiner} "
                     "below is met:")
        out += _group_block(doc, goal_live, by_id, phase)

    act = stopping.active(doc, phase=phase)
    if act:
        out.append(
            "Your finish summary MUST account for each ACTIVE condition ("
            + ", ".join(c["id"] for c in act)
            + "): a line `[s<n>] met — <evidence>` or `[s<n>] unmet — <why>` per condition. "
              "A finish that skips one is rejected and costs a turn. For a FINAL GOAL condition "
              "the `unmet` note is where you state the distance — what remains, and what it is "
              "waiting on. Conditions marked waiting are NOT yours to account for yet — they "
              "become active when what they wait on is met.")
    verdict = stopping.evaluate(doc)
    if verdict["goal_satisfied"]:
        out.append("EVERY final-goal condition is met — this ROUTINE is finished. Say so in your "
                   "summary and finish now; the engine stops scheduling it and asks the user to "
                   "confirm its retirement. Do not start new work against a goal already reached.")
    elif goal_live:
        out.append("The final goal is NOT yet met. Do not mark a goal condition met to close the "
                   "job out — a goal marked met retires the routine, and the claim is checked "
                   "against this run's own transcript.")
    if run_live:
        out.append('A met LIMIT condition ("only diagnose", "stop once X is verified") means '
                   "finish NOW rather than continue past it.")
    return "\n".join(out)
