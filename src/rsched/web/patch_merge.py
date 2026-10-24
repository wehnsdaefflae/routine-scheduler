"""Merging a PARTIAL config block into the stored one — and judging the RESULT.

Split out of `api_routine_patch.py` (one responsibility per file, like `patch_bindings.py`):
two of that module's appliers take a partial block because their controls send one — the
ladder control toggles `enabled` without resending the rung height, the gate editor toggles
`enabled` without resending the checks — so neither body can be judged on its own.

What brought them here (F759) is what they did with the MERGE. Both judged the PATCH's keys
and then merged, which left the STORED block's vocabulary unjudged:

- `ladder` refused an unknown key in the patch and then merged the patch over the stored block,
  so a key the stored block already carried survived every save. `load_routine` reports it
  forever (`ladder.max_depth: unknown ladder key`) and no patch could clear it: the key is not
  in `LADDER_KEYS`, so naming it — even as a null — is itself a 400. A rename (D189, 2026-10-22)
  therefore left a routine permanently unrepairable through the API; `fau` is the specimen, and
  the operator's own `{"ladder": {"max_depth": null}}` attempt was refused by that check.
- `run_gate` carries the worse half of the same bug: its merge goes through `RunGateConfig`,
  which is `extra="forbid"`, so one stale stored key makes EVERY save a 422 — unsavable as well
  as unrepairable.

So the vocabulary is enforced on the RESULT, which is the only place that describes what will be
WRITTEN, and it is the same vocabulary the loader scrubs against (`config/routine_scrub.py`) —
one set, read at both ends, instead of a writer that accepts what a loader then reports.

The patch's own keys stay REFUSED: a typo is a save that would otherwise answer "updated" and
do nothing, so it has to be loud. A key only the stored block carries is the opposite case —
nobody is typing it, it is already written, and refusing it buys a 400 per save without ever
making the routine repairable. It is dropped, and NAMED to the caller (`repaired` in the PATCH
response), so a repair is learned from the observation rather than from a problem line that
quietly stopped appearing.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..config.base import DEFAULT_LADDER, LADDER_KEYS
from ..config.routine import RunGateConfig

#: `run_gate`'s whole vocabulary, taken from the model that validates the merge rather than
#: restated — a field added there is accepted here without a second edit.
RUN_GATE_KEYS = frozenset(RunGateConfig.model_fields)


def merge_block(stored: dict, patch: object, known: frozenset[str],
                label: str) -> tuple[dict, list[str]]:
    """Merge `patch` over `stored`, enforce `known` on the RESULT, and return (result, repaired).

    `repaired` names every key dropped from the result as unknown, qualified with `label`
    (`ladder.max_depth`) — the caller reports it. A key the PATCH names outside `known` is a
    400 instead: see the module docstring for why the two cases end differently.
    """
    if not isinstance(patch, dict):
        raise HTTPException(400, f"{label}: expected a mapping of {sorted(known)}")
    if unknown := sorted(set(patch) - known):
        raise HTTPException(400, f"{label}: unknown key(s) {unknown} "
                                 f"(expected {sorted(known)})")
    merged = {**stored, **patch}
    repaired = [f"{label}.{key}" for key in sorted(set(merged) - known)]
    return {key: val for key, val in merged.items() if key in known}, repaired


def apply_ladder(raw: dict, updates: dict) -> list[str]:
    """Merge a partial `ladder` block into the stored one and validate the RESULT.

    CONFIG, not tuning: authority over being watched is the user's. A partial patch is the
    normal case (the control toggles `enabled` without resending the depth), so the body alone
    cannot be judged.

    Every bad value is REFUSED here rather than left to `load_routine`, which degrades an
    unreadable block toward OFF — the safe direction at load time, and the wrong one for a save:
    a user who typed `rung_height: 0` and was told "saved" would get a silently disabled ladder.

    The stored block is read over the DEFAULTS, exactly as the loader reads it
    (`routine_scrub._scrub_ladder`), so a block holding nothing but a stale key still merges
    into a complete one instead of failing `enabled`. Returns the keys repaired out of it.
    """
    block = raw.get("ladder")
    stored = {**DEFAULT_LADDER, **(block if isinstance(block, dict) else {})}
    merged, repaired = merge_block(stored, updates.pop("ladder") or {}, LADDER_KEYS, "ladder")
    if not isinstance(merged.get("enabled"), bool):
        raise HTTPException(400, "ladder.enabled: expected true or false")
    height = merged.get("rung_height")
    if isinstance(height, bool) or not isinstance(height, int) or height < 1:
        raise HTTPException(400, f"ladder.rung_height: expected a whole number of rungs of at "
                                 f"least 1 (got {height!r}) — it is also floored by "
                                 "budgets.max_subrun_depth at run time")
    raw["ladder"] = merged
    return repaired


def apply_run_gate(raw: dict, updates: dict) -> list[str]:
    """Merge a partial `run_gate` into the stored one and validate the RESULT.

    A partial patch is the normal case — the console toggles `enabled` without resending the
    checks — so the body alone cannot be judged: `{enabled: true}` is sound over a stored check
    list and unsound over none. The merge is validated as a whole and written as a whole; `checks`
    REPLACES the stored list, like every list this endpoint takes. Pops the key, so the generic
    merge in `apply_updates` never sees it. Returns the keys repaired out of the stored block —
    without which one of them is a 422 on every save, `RunGateConfig` being `extra="forbid"`.
    """
    stored = raw.get("run_gate")
    merged, repaired = merge_block(stored if isinstance(stored, dict) else {},
                                   updates.pop("run_gate"), RUN_GATE_KEYS, "run_gate")
    try:
        raw["run_gate"] = RunGateConfig.model_validate(merged).model_dump()
    except ValueError as exc:
        raise HTTPException(422, f"run_gate: {exc}") from exc
    return repaired
