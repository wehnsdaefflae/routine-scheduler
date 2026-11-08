"""The digest's NEXT FIRE'S GATE block — the run is asked to write the checks for the fire after
it (`rsched/gatecompose.py`, docs/run-gates.md "Run-written gates"). Prompt text only;
`docs/prompt-anatomy.md` pins the wording and `tests/test_prompt_anatomy.py` fails on drift.

Said ONCE, at boot, beside the FINISH block and for the same reason the D200 recipe ask is
there: the harness contract is held to a shrink-only budget, and this is a question about the
routine's own future that only a depth-0 routine run can answer. Absent wherever it could not
take effect — a run that asks no recipe revisions (a child, a conversation, a detached task;
`recipeproposal.asks_for_revisions`), a routine whose gate or whose `run_written` switch is
off, and a routine whose gate
carries no `max_quiet` floor, under which a run-written gate is never used.

The block shows the run what it needs to write a gate that can actually skip: what is in force
for the next fire and whether its last one was set aside, how the gate has done (skips, fires
admitted before any check, checks that have never answered "no work" — `gate_yield.py`), the
routine's own checks to copy secret and path names from, and the check vocabulary, rendered
from `gatekit.KINDS` so it cannot drift from what the gate accepts.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import gatecompose, gatekit
from ..readmodels import gate_yield

#: The file's own content is shown up to this many characters; a gate is a short list.
SHOWN_MAX = 1500


def _vocabulary() -> list[str]:
    out = []
    for kind, (meaning, spec) in gatekit.KINDS.items():
        if kind == "script":
            continue
        params = ", ".join(f"{n}{'*' if req else ''}" for n, (_t, req, _h) in spec.items())
        out.append(f"  - {kind}({params}) — {meaning}")
    return out


def _standing(cfg) -> list[str]:
    checks, source = gatecompose.compose(cfg)
    if source["kind"] == "run":
        lines = [(f"  In force for the next fire: the gate a run wrote ({len(checks)} checks, "
                  f"backstop {checks[-1]['days']} days) — why: {source['why'] or '(none given)'}")]
    elif source.get("rejected"):
        lines = [("  In force for the next fire: the routine's own gate — the written one is "
                  f"set aside: {source['rejected']}")]
    else:
        lines = ["  In force for the next fire: the routine's own gate (no run has written one)"]
    r = gate_yield.reading(cfg.dir)
    if r["last_rejected"] and not source.get("rejected"):
        lines.append(f"  The last fire set a written gate aside: {r['last_rejected']}")
    if r["judged"]:
        lines.append(f"  Its last {r['judged']} judged fires: {r['skipped']} skipped, "
                     f"{r['before_checks']} admitted before any check was asked")
    if r["run_written"]["fires"]:
        lines.append(f"  Of those, {r['run_written']['fires']} asked a gate a run wrote, and "
                     f"{r['run_written']['skipped']} of them skipped")
    lines += [(f"  A routine check that never let a fire skip: {c['id']} ({c['kind']}) "
               f"answered work on all {c['asked']} asks — newest: {c['last']}")
              for c in r["never_idle"]]
    return lines


def block(ctx) -> str:
    """The block as the digest's tail (a blank line, then `section`), or "" for a run that
    asks no recipe revisions — the same predicate as the D200 ask, so the two cannot disagree
    about who is a routine run with a future of its own.
    """
    from .recipeproposal import asks_for_revisions

    if not asks_for_revisions(ctx):
        return ""
    sec = section(ctx.routine)
    return "\n\n" + sec if sec else ""


def section(cfg) -> str:
    """The block, or "" where a written gate could not take effect."""
    gate = cfg.run_gate
    floor = gatecompose.floor_days(list(gate.checks))
    if not gate.enabled or not gate.run_written or floor is None:
        return ""
    path = Path(cfg.dir) / gatecompose.FILE
    try:
        current = path.read_text(encoding="utf-8")[:SHOWN_MAX] if path.is_file() else ""
    except (OSError, UnicodeDecodeError):
        current = "(unreadable)"
    own = json.dumps(list(gate.checks), ensure_ascii=False)
    return "\n".join([
        (f"NEXT FIRE'S GATE ({gatecompose.FILE} — what would make this routine's NEXT "
         "scheduled fire worth running; that fire is skipped, at no cost, when none of it "
         "holds):"),
        *_standing(cfg),
        ("Before you finish, write that file from what THIS run learned: "
        '{"why": "<what the next run would have to do>", "checks": [<check>, …]}. Its checks '
        "REPLACE the routine's own for the next fire. Watch the SOURCES your next work comes "
        "from — who will write, which file or feed will change, which date falls due — not a "
        "backlog this run could not drain: a check that finds work on every fire never lets one "
        "skip. Leave the file as it is when it is still right; delete it to hand the next fire "
        "back to the routine's own gate. Asked whatever you write: inbox freight, answers and "
        "store notes, a run that did not finish ok, a changed recipe, and the routine's "
        f"max_quiet backstop of {floor} days, which yours may shorten and never lengthen. A "
        "check that cannot establish its answer counts as work; a path or secret the routine "
        "is not granted sets the whole gate aside."),
        *([f"Its current content: {current}"] if current else []),
        f"The routine's own checks: {own}",
        "Check kinds (* = required; a check may also carry `id` and the `job` it watches):",
        *_vocabulary(),
    ])
