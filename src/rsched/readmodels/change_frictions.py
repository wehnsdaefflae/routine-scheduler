"""Did a change remove the failure it met? — the MOMENT TEST beside the rate verdict.

`change_effects` judges a change on rates (readmodels/change_signals.py), which need `MIN_RUNS`
on each side of ONE behaviour key, and a routine whose recipe changes every second run never
gets there: 96% of this instance's changes read `too few runs`. Harness² (arXiv 2610.14224)
judges an edit on its MECHANISM instead — a failure that repeated across the runs before it is
a real property of the harness, one seen once is noise, and the edit is kept when that failure
does not come back. This module asks exactly that, over the FRICTION signatures every run
records (engine/frictions.py):

- a signature is CHRONIC before the change when at least `CHRONIC` of the runs before it carry
  it; one run's slip is never a target;
- after the change it is `fixed` (in none of the runs after) or `persists` (in some);
- a signature in no run before and in at least `CHRONIC` after was `introduced`.

A side needs `SIDE` runs, not `MIN_RUNS` — the question is a yes/no about one failure, not a
rate. Where the change's own segment is too short, the window reaches across the next change
(or the previous one) until it has them, and every change it crossed is named in `confounded`:
the verdict then belongs to those changes TOGETHER, which is what Harness²'s reflector is told
to say ("prefer `confounded` whenever several components changed at once") and what a reader
must see before crediting one of them.

A change may also say what it was AIMED at: a recipe commit carrying an `Addresses:` line
(`Addresses: edit_miss, script:sync`) names the signatures it meant to remove. Each aim gets
its own verdict — `fixed`, `persists`, or `unfounded` when the runs before the change did not
repeat it ("an edit that cannot name the moment is speculation"). A run with no recorded
frictions (before the record existed, or no transcript to read) is UNKNOWN and is not counted
on either side — never read as a clean run.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .. import libgit
from ..recipes import RECIPE_PATHSPECS
from .change_keys import FullKey

#: Runs before a change that must carry a signature for it to be the harness's, not chance.
CHRONIC = 2
#: Runs each side needs before anything is said.
SIDE = 2
#: The most runs one side reads (change_fleet.WINDOW).
REACH = 5

SIGNATURE = re.compile(r"(schema|edit_miss|read_miss|memory_cap"
                       r"|(script|util|refused|deferral|budget):[\w.\-]+|unmet:d\d+)")
ADDRESSES = re.compile(r"^addresses:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

Segment = tuple[FullKey, list[dict]]


def frictions_of(rec: dict) -> dict | None:
    q = rec.get("quality")
    found = q.get("frictions") if isinstance(q, dict) else None
    return found if isinstance(found, dict) else None


def _gather(segs: list[Segment], first: int, step: int) -> tuple[list[dict], list[str]]:
    """Known runs from segment `first` outward (`step` -1 = back in time, +1 = forward), up to
    `REACH`, crossing into the next segment only while fewer than `SIDE` are known — and the
    changes crossed, by the run that opened each.
    """
    runs: list[dict] = []
    crossed: list[str] = []
    j = first
    while 0 <= j < len(segs) and len(runs) < SIDE:
        if j != first:
            crossed.append(str(segs[j if step > 0 else j + 1][1][0].get("run_id") or ""))
        seg = segs[j][1] if step > 0 else segs[j][1][::-1]
        for rec in seg:
            if len(runs) >= REACH:
                break
            if frictions_of(rec) is not None:
                runs.append(rec)
        j += step
    return (runs if step > 0 else runs[::-1]), crossed


def _present(runs: list[dict]) -> Counter[str]:
    """Per signature, how many of the runs carry it — once a run, however often it happened."""
    return Counter(s for r in runs for s in (frictions_of(r) or {}))


def aims(routine_dir: Path | None, what: list[dict]) -> list[str] | None:
    """The signatures the change's recipe commits say they address, or None when they cannot
    be read (no routine dir, no git, an unknown commit) — unknown, never "none declared".
    """
    recipe = next((w for w in what if w.get("kind") == "recipe"), None)
    if recipe is None:
        return []
    if routine_dir is None or not recipe.get("from") or not recipe.get("to"):
        return None
    done = libgit.git(routine_dir, "log", "--format=%B",
                      f"{recipe['from']}..{recipe['to']}", "--", *RECIPE_PATHSPECS)
    if done.returncode != 0:
        return None
    found: list[str] = []
    for line in ADDRESSES.findall(done.stdout):
        for token in re.split(r"[,\s]+", line.strip()):
            if SIGNATURE.fullmatch(token) and token not in found:
                found.append(token)
    return found


def judge(segs: list[Segment], i: int, what: list[dict],
          routine_dir: Path | None) -> dict:
    """The moment test for the change opening segment `i` (module docstring)."""
    before, back = _gather(segs, i - 1, -1)
    after, ahead = _gather(segs, i, +1)
    b, a = _present(before), _present(after)
    chronic = {s: n for s, n in b.items() if n >= CHRONIC}
    if len(before) < SIDE:
        status = "too few runs"
    elif len(after) < SIDE:
        status = "measuring" if i + len(ahead) >= len(segs) - 1 else "too few runs"
    else:
        status = "judged"

    def row(sig: str) -> dict:
        return {"signature": sig, "before": b.get(sig, 0), "after": a.get(sig, 0)}

    out: dict = {"status": status, "runs_before": len(before), "runs_after": len(after),
                 "confounded": [*back, *ahead], "chronic": [row(s) for s in sorted(chronic)]}
    if status == "judged":
        out["fixed"] = [row(s) for s in sorted(chronic) if not a.get(s)]
        out["persists"] = [row(s) for s in sorted(chronic) if a.get(s)]
        out["introduced"] = [row(s) for s in sorted(a) if s not in b and a[s] >= CHRONIC]
    named = aims(routine_dir, what)
    out["aims"] = None if named is None else [
        {**row(s), "verdict": ("unfounded" if s not in chronic else
                               "measuring" if status != "judged" else
                               "fixed" if not a.get(s) else "persists")}
        for s in named]
    return out
