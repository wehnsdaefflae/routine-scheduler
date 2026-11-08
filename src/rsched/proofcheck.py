"""CHECKING a decision's proof — the half that turns D220's promise into a fact.

`rsched/proofs.py` holds the vocabulary and the state machine; this module runs the checks. For
each answered decision whose chosen option sits `in_progress`, it runs that option's predicate
and writes `positive` (the proof is there, so the actions can be assumed performed) or
`negative` (the actions were performed and the proof is STILL absent — the operator's own
"unexpected event" state, and the case nothing on this instance notices today).

## Why the registry is small, and why an unknown predicate changes nothing

A predicate is a named, computable check. Exactly one is implementable from what a decision
record already carries:

* `config_patch_live` — the decision proposed a `config_patch`, so the proof is that the live
  config holds those values. It is a PATCH, so it is compared key by key: the live config holds
  far more than the patch names, and a whole-object comparison would call every real answer
  negative. `config_target` (D123/F458) decides WHOSE config is read — reading the asker's own
  would prove the wrong thing, forever negatively.

Everything else a routine might promise — a release that names an item, a row that was dropped,
a file that exists — is a predicate THAT routine can compute and this module cannot, because
the evidence lives in its own state. So an id with no entry here is `unknown` and the sweep
LEAVES THE STATE ALONE. A check that cannot run must never read as a check that failed:
otherwise every predicate nobody has implemented turns into a false alarm on the operator's
page, which is the opposite of what D220 asks for.

## What the sweep writes, and what it must never touch

Only `questions/answered/*.json` — the durable archive that is what a settled card is rebuilt
from (F525) — and only the `proof` map inside it. A record with no proof is not rewritten at
all: every decision the fleet has ever answered is in that state, and re-stamping settled
history on the day this ships would be a change nobody asked for.

A `positive` proof is never re-checked (it cleared the row; the work landed). A `negative` one
IS, every sweep: the operator applies a patch a day later, and a loud state that cannot clear
itself is a state he learns to ignore.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from . import proofs
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.proofcheck")


def _live_config(record: dict, rdir: Path, routines_home: Path | None) -> dict:
    """The config the patch would have landed in — the target's, or the asker's own."""
    import yaml
    target = str(record.get("config_target") or "").strip()
    path = ((routines_home / target / "routine.yaml")
            if target and routines_home else rdir / "routine.yaml")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _holds(live: object, patch: dict) -> bool:
    """Does `live` hold every key the patch names, to the value it names? Key by key.

    A patch body is a partial document: `{"budgets": {"max_turns": 500}}` says nothing about
    `max_minutes`, and a whole-object comparison would therefore read every applied patch as
    not applied.
    """
    if not isinstance(live, dict):
        return False
    for key, want in patch.items():
        have = live.get(key)
        if isinstance(want, dict):
            if not _holds(have, want):
                return False
        elif have != want:
            return False
    return True


def config_patch_live(record: dict, *, rdir: Path, routines_home: Path | None) -> bool:
    """Is the `config_patch` this decision proposed LIVE in the config it targets?

    False when the record carries no patch at all: such a decision should not have been filed
    with this predicate, and claiming its proof positive would be the silent assumption the
    whole lifecycle exists to remove.
    """
    patch = record.get("config_patch")
    if not isinstance(patch, dict) or not patch:
        return False
    return _holds(_live_config(record, rdir, routines_home), patch)


#: Predicate id → the check. Every entry needs a real reader (the sweep below is it) and a
#: documented reading, because a routine filing a decision chooses from this table.
PREDICATES: dict[str, Callable[..., bool]] = {"config_patch_live": config_patch_live}


def known(predicate: str) -> bool:
    """Can this instance check that predicate? — for a surface that must say so, and for a
    routine choosing one at filing time.
    """
    return str(predicate) in PREDICATES


def run_predicate(predicate: str, record: dict, *, rdir: Path | None = None,
                  routines_home: Path | None = None) -> bool:
    """Run one predicate by id. KeyError for an unknown one — the sweep asks `known` first,
    so reaching this with a bad id is a caller's bug and must be loud.
    """
    if predicate not in PREDICATES:
        raise KeyError(f"no proof predicate {predicate!r} — this instance can check "
                       f"{sorted(PREDICATES)}")
    return PREDICATES[predicate](record, rdir=rdir or Path(), routines_home=routines_home)


def _outstanding(proof: dict) -> tuple[str, dict]:
    """The option whose proof is still owed — `in_progress`, or `negative` (re-checked every
    sweep, so a decision he carried out late can clear itself). ("", {}) when none is.
    """
    for option, entry in proof.items():
        if isinstance(entry, dict) and entry.get("state") in (proofs.IN_PROGRESS,
                                                              proofs.NEGATIVE):
            return str(option), entry
    return "", {}


def check_record(record: dict, *, rdir: Path,
                 routines_home: Path | None = None) -> dict | None:
    """That record with its proof moved on, or None when nothing moved.

    None covers every reason to leave it alone: no proof, nothing outstanding, an unknown
    predicate, or a predicate that raised. The caller writes only what it is given, so a
    record this returns None for is not rewritten at all.
    """
    proof = record.get("proof")
    if not isinstance(proof, dict) or not proof:
        return None
    option, entry = _outstanding(proof)
    if not option:
        return None
    predicate = str(entry.get("predicate") or "")
    if not known(predicate):
        return None
    try:
        found = run_predicate(predicate, record, rdir=rdir, routines_home=routines_home)
    except Exception:
        # Never let one routine's unreadable config stop the sweep over the others, and never
        # record a verdict an exception produced: an inference resting on nothing is worse
        # than an honest "still in progress".
        log.exception("proof predicate %s raised for %s", predicate, record.get("qid"))
        return None
    if entry.get("state") == (proofs.POSITIVE if found else proofs.NEGATIVE):
        return None                      # already says that; re-stamping would be noise
    return {**record, "proof": proofs.checked(proof, option, found=found)}


def sweep_routine(rdir: Path, *, routines_home: Path | None = None) -> int:
    """Check every answered decision of ONE routine; returns how many proofs moved.

    Reads the durable archive (`questions/answered/`), which is the record that survives the
    run that consumed the answer — the pending file is gone by then (F525).
    """
    adir = Path(rdir) / "questions" / "answered"
    if not adir.is_dir():
        return 0
    moved = 0
    for path in sorted(adir.glob("*.json")):
        record = read_json(path)
        if not isinstance(record, dict) or not record.get("answered"):
            continue
        updated = check_record(record, rdir=Path(rdir), routines_home=routines_home)
        if updated is None:
            continue
        atomic_write_json(path, updated)
        moved += 1
        log.info("proof %s of %s is now %s", record.get("qid"), Path(rdir).name,
                 proofs.state_of(updated))
    return moved


def sweep_home(routines_home: Path) -> int:
    """Every routine of one home; returns how many proofs moved in total."""
    home = Path(routines_home)
    if not home.is_dir():
        return 0
    return sum(sweep_routine(d, routines_home=home)
               for d in sorted(home.iterdir()) if (d / "routine.yaml").is_file())
