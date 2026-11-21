"""D281: a routine DRAFT that names a path the engine owns says so BEFORE the routine exists."""

from __future__ import annotations

from rsched import runledger
from rsched.engine import draftlint, obligations


def test_a_draft_naming_the_obligations_store_is_warned_about():
    """The exact live shape: the register pointed at the engine's own file."""
    draft = {"instruction": "Keep the obligations register at state/obligations.json fresh.",
             "done_when": ["state/obligations.json lists every open promise"]}
    keys = draftlint.preview_keys(draft)
    assert [f["path"] for f in keys["reserved_paths"]] == [str(obligations.FILE)]
    assert keys["reserved_paths"][0]["mentions"] == 2, "both the instruction and the Done when"
    assert "obligations" in keys["reserved_warning"]


def test_the_warning_says_what_owns_the_path_and_that_nothing_is_blocked():
    """A warning that does not name the alternative costs the reader the same turn the refusal
    would have — and one read as a veto sends the run looking for an override.
    """
    keys = draftlint.preview_keys({"instruction": "write state/obligations.json each run"})
    warning = keys["reserved_warning"]
    assert "Nothing is blocked here" in warning
    assert obligations.REFUSAL.split(" — ")[0][:20] in warning or "obligation" in warning
    assert "FIRST run" in warning, "the cost this check exists to prevent"


def test_every_field_that_carries_prose_is_scanned():
    """A path named in `done_when` is exactly as broken as one in the instruction: that line is
    accounted for at every finish. `never`, `setup` and `finish_line` are read for the same
    reason — a run meets the refusal wherever the recipe sent it.
    """
    for field in ("instruction", "done_when", "never", "setup", "finish_line"):
        value = "state/obligations.json is the register"
        draft = {field: value if field == "instruction" else [value]}
        keys = draftlint.preview_keys(draft)
        assert keys.get("reserved_paths"), f"{field} was not scanned"


def test_the_memory_seal_and_the_config_file_are_screened_too():
    """Both are reserved by a different mechanism than the store seal (the memory actions own
    one, the user owns the other), and both are things a draft writes by mistake.
    """
    keys = draftlint.preview_keys({
        "instruction": "Keep notes in .memory/topic.md and update routine.yaml when the "
                       "cadence changes."})
    paths = {f["path"] for f in keys["reserved_paths"]}
    assert paths == {".memory/", "routine.yaml"}
    assert "memory_write" in keys["reserved_warning"]
    assert "config_patch" in keys["reserved_warning"]


def test_the_process_record_is_screened():
    """`LEDGER.md` and its archive are written by the engine from each finished run's summary."""
    keys = draftlint.preview_keys({"instruction": f"append the digest to {runledger.FILE}"})
    assert [f["path"] for f in keys["reserved_paths"]] == [runledger.FILE]


def test_a_clean_draft_adds_nothing_to_the_observation():
    """The common case. Keys that are always present make a reader skim past them, and the
    preview is already long — so a clean draft contributes no key at all.
    """
    assert draftlint.preview_keys({
        "instruction": "Each run, read the folder and write state/seen.json.",
        "done_when": ["state/report.md is written"]}) == {}
    assert draftlint.findings("") == []
    assert draftlint.warning([]) == ""


def test_a_similar_path_that_is_not_reserved_is_not_warned_about():
    """The alternative the warning steers toward must not itself trip the check, or the author
    is told the fix is also wrong.
    """
    assert draftlint.preview_keys({
        "instruction": "the register lives at state/obligations-register.json"}) == {}
    assert draftlint.preview_keys({"instruction": "state/my-jobs.json holds the queue"}) == {}


def test_the_screened_list_is_the_engines_own():
    """Read from `engine/storeseal`'s own constants rather than restated here: a second list
    would go stale the first time a store was added, and nothing would say so.
    """
    from rsched.jobs import JOBS_FILE

    found = {f["path"] for f in draftlint.findings(
        f"{obligations.FILE} {runledger.FILE} {runledger.ARCHIVE_DIR} {JOBS_FILE}")}
    assert found == {str(obligations.FILE), runledger.FILE, runledger.ARCHIVE_DIR, JOBS_FILE}
