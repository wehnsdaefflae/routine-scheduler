"""A renamed key must be REPAIRABLE through the API — the writer judges the MERGED result.

F759. Three writers merged a partial block into the stored one and judged the PATCH's keys,
never the result, so the STORED block's vocabulary was nobody's business:

- `ladder` refused an unknown patch key and then merged the patch over the stored block. A key
  the stored block already carried survived every save, `load_routine` reported it forever, and
  no patch could clear it — the key is not in `LADDER_KEYS`, so naming it (even as a null) was
  itself a 400. D189 renamed `max_depth` → `rung_height` on 2026-10-22 and left `fau`
  permanently unrepairable: the operator's own `{"ladder": {"max_depth": null}}` was refused.
- `run_gate` merges through `RunGateConfig`, which is `extra="forbid"`: one stale stored key
  made EVERY save a 422 — unsavable as well as unrepairable.
- `write_tuning` merged updates into the raw file and dropped nothing, so `load_tuning` reported
  a pre-rename key (`fau`: `ladder_rung_height`) after every write.

The rule these pin: a key the PATCH names outside the known set stays a 400 (a typo must be
loud — it is a save that would otherwise report success and do nothing), and a key only the
STORED block carries is dropped from the result and NAMED to the caller. Nobody is typing it,
it is already written, and refusing it buys a 400 per save without ever making the routine
repairable.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from helpers import routine_yaml
from rsched.config import load_routine, load_tuning, write_tuning
from rsched.config.base import LADDER_KEYS, TUNING_KEYS
from rsched.config.routine_scrub import scrub_bindings
from rsched.web.patch_merge import apply_ladder, apply_run_gate

#: What `fau` carried on 2026-10-24: a web edit on 2026-10-16 wrote the then-current spellings,
#: D189 renamed both on 2026-10-22, and the two keys have been unreachable ever since.
FAU_LADDER = {"enabled": True, "max_depth": 3, "rung_height": 3}
FAU_TUNING = {"ladder_rung_height": 50, "ladder_turn_interval": 50}


# -- the ladder block: a save repairs the stored vocabulary -----------------------------------


def test_a_save_drops_a_stale_stored_ladder_key_and_names_it():
    """THE defect. An ordinary save — the control resending the rung height — must clear the
    pre-rename key, and say so, rather than write it back untouched for the loader to report."""
    raw = {"ladder": dict(FAU_LADDER)}
    repaired = apply_ladder(raw, {"ladder": {"rung_height": 5}})
    assert raw["ladder"] == {"enabled": True, "rung_height": 5}
    assert repaired == ["ladder.max_depth"]


def test_a_stored_block_of_nothing_but_a_stale_key_still_merges_whole():
    """The repair may not depend on the stored block being otherwise complete: a block holding
    only the renamed key would fail the `enabled` check and stay unrepairable. The stored block
    is read over the DEFAULTS, exactly as `routine_scrub._scrub_ladder` reads it."""
    raw = {"ladder": {"max_depth": 9}}
    repaired = apply_ladder(raw, {"ladder": {"enabled": True}})
    assert raw["ladder"] == {"enabled": True, "rung_height": 3}
    assert repaired == ["ladder.max_depth"]


def test_the_repaired_block_is_exactly_what_the_loader_accepts():
    """One vocabulary read at both ends: what the writer leaves behind is what the loader scrubs
    against, so a save cannot produce a block the next load complains about."""
    raw = {"ladder": dict(FAU_LADDER)}
    apply_ladder(raw, {"ladder": {"enabled": False}})
    assert set(raw["ladder"]) == set(LADDER_KEYS)


@pytest.mark.parametrize("patch", [{"max_depth": 3}, {"bogus": 1}])
def test_a_stale_or_mistyped_key_sent_in_the_patch_is_still_refused(patch):
    """The other half of the rule, and the reason this is not "accept everything": a key the
    CALLER sends outside the known set is a typo, and a typo answered `updated` would be a save
    that reported success and did nothing."""
    with pytest.raises(HTTPException) as exc:
        apply_ladder({"ladder": dict(FAU_LADDER)}, {"ladder": dict(patch)})
    assert exc.value.status_code == 400 and "unknown key" in str(exc.value.detail)


def test_a_clean_save_repairs_nothing_and_says_nothing():
    raw = {"ladder": {"enabled": True, "rung_height": 3}}
    assert apply_ladder(raw, {"ladder": {"rung_height": 2}}) == []


# -- the run_gate block: the same defect, one step worse --------------------------------------


def test_a_stale_stored_run_gate_key_does_not_make_every_save_a_422():
    """`RunGateConfig` is `extra="forbid"`, so the merge itself refused the stored block — a
    renamed gate key would take the whole gate editor down for that routine, not merely leave a
    problem line behind."""
    raw = {"run_gate": {"enabled": False, "timeout_s": 30, "checks": [], "poll_s": 5}}
    repaired = apply_run_gate(raw, {"run_gate": {"timeout_s": 60}})
    assert raw["run_gate"]["timeout_s"] == 60 and "poll_s" not in raw["run_gate"]
    assert repaired == ["run_gate.poll_s"]


def test_a_mistyped_run_gate_key_sent_in_the_patch_is_refused_not_dropped():
    with pytest.raises(HTTPException) as exc:
        apply_run_gate({}, {"run_gate": {"timeuot_s": 60}})
    assert exc.value.status_code == 400 and "unknown key" in str(exc.value.detail)


# -- tuning.yaml: the third writer ------------------------------------------------------------


def test_write_tuning_drops_a_key_outside_the_vocabulary_and_names_it(tmp_path):
    """`load_tuning` reports an unknown key on every load; the writer that keeps rewriting it is
    what makes the report permanent. `fau`'s `ladder_rung_height` is the specimen."""
    (tmp_path / "tuning.yaml").write_text(yaml.safe_dump(FAU_TUNING), encoding="utf-8")
    repaired = write_tuning(tmp_path, {"ladder_turn_interval": 60})
    values, problems = load_tuning(tmp_path)
    assert repaired == ["ladder_rung_height"]
    assert not problems, problems
    assert values == {"ladder_turn_interval": 60}


def test_write_tuning_still_honours_none_as_a_removal(tmp_path):
    """The precedent this follows, unchanged: `oversight_turns: 0` is written as a removal,
    because absence is that knob's one spelling of its derived default."""
    write_tuning(tmp_path, {"oversight_turns": 7, "ladder_turn_interval": 50})
    assert write_tuning(tmp_path, {"oversight_turns": None}) == []
    assert load_tuning(tmp_path)[0] == {"ladder_turn_interval": 50}


def test_every_tuning_key_the_writer_keeps_is_one_the_loader_reads(tmp_path):
    write_tuning(tmp_path, {k: 9 for k in TUNING_KEYS if k != "deliberation"})
    raw = yaml.safe_load((tmp_path / "tuning.yaml").read_text(encoding="utf-8"))
    assert not set(raw) - set(TUNING_KEYS)


# -- the specimen, end to end through the real PATCH edge -------------------------------------


def test_one_save_through_the_api_repairs_both_of_faus_renamed_keys(api_client, make_routine):
    """The operator's live attempt (2026-10-23, q-20261023-010002-341: "apply the null patch")
    made reachable. A routine carrying BOTH pre-rename keys is repaired by one ordinary save,
    the response names what it cleared, and the next load reports nothing."""
    c, _ = api_client
    root = make_routine(slug="specimen")
    (root / "routine.yaml").write_text(
        yaml.safe_dump({**routine_yaml(root), "ladder": dict(FAU_LADDER)}), encoding="utf-8")
    (root / "tuning.yaml").write_text(yaml.safe_dump(FAU_TUNING), encoding="utf-8")
    # `load_routine` carries the tuning problems too, so one list holds both of fau's
    cfg, problems = load_routine(root)
    assert sorted(problems) == [
        "ladder.max_depth: unknown ladder key (expected one of ['enabled', 'rung_height'])",
        "tuning.yaml ladder_rung_height: unknown tuning key"], problems

    r = c.patch("/api/routines/specimen",
                json={"ladder": {"enabled": True, "rung_height": 2}, "ladder_turn_interval": 60})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["repaired"]) == ["ladder.max_depth", "tuning.ladder_rung_height"]

    cfg, problems = load_routine(root)
    assert not problems, problems
    assert cfg.ladder == {"enabled": True, "rung_height": 2}
    assert load_tuning(root) == ({"ladder_turn_interval": 60}, [])


def test_a_routine_with_nothing_to_repair_is_not_told_it_repaired_something(api_client,
                                                                           make_routine):
    """`repaired` is an exception report, not a field on every response: an ordinary save on a
    clean routine must not grow a key the console would have to learn to ignore."""
    c, _ = api_client
    make_routine(slug="clean")
    r = c.patch("/api/routines/clean", json={"ladder": {"enabled": True}})
    assert r.status_code == 200 and "repaired" not in r.json()


def test_a_tuning_only_save_reports_its_repair_too(api_client, make_routine):
    """A tuning-only patch returns early without rewriting routine.yaml — its own return, which
    is exactly the kind of second exit a repair report gets forgotten in."""
    c, _ = api_client
    root = make_routine(slug="tuned")
    (root / "tuning.yaml").write_text(yaml.safe_dump(FAU_TUNING), encoding="utf-8")
    r = c.patch("/api/routines/tuned", json={"ladder_turn_interval": 60})
    assert r.status_code == 200, r.text
    assert r.json()["repaired"] == ["tuning.ladder_rung_height"]
    assert load_tuning(root) == ({"ladder_turn_interval": 60}, [])


# -- the guard that keeps the two vocabularies one ---------------------------------------------


def test_the_writers_vocabulary_is_the_loaders_vocabulary():
    """What made F759 possible was two sets: the writer's `LADDER_KEYS` check on the patch and
    the loader's on the file. They are the same frozenset today, and this fails if a future
    edit gives the writer a set of its own again."""
    from rsched.web import patch_merge

    assert patch_merge.LADDER_KEYS is LADDER_KEYS
    assert scrub_bindings.__module__ == "rsched.config.routine_scrub"
