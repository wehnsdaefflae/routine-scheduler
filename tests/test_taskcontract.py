"""A contract names only the task (P2) — the fixed vocabulary in `workflows/taskcontract.py`.

Every line below is REAL-SHAPED: taken from, or written in the shape of, the live fleet's
recipes as measured on 2026-11-06, so the check is held to the sentences it has to judge and not
to toy ones. Both directions are pinned, because the failure that would switch this check off is
a false positive on a product line — a watchlist, a manifest, an evaluation ledger's rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rsched.config.outcome import OutcomeConfig
from rsched.engine import outcome_words
from rsched.workflows import generate, pipeline_prompts, taskcontract
from rsched.workflows.lint import lint_workflow_py
from rsched.workflows.pyworkflow import parse_py

SEED = Path(__file__).resolve().parents[1] / "library-seed" / "workflows"

# ---- a Done-when line about nothing but the run's own record --------------------------------

PROCESS_ONLY = [
    (("this run's LEDGER.md entry names the tracks touched, what changed, candidates rejected "
     "with their reasons, and held-out numbers"), "a LEDGER entry"),
    (("the finish report names every pass audited per repo, what shipped per repo, what was "
     "deliberately left with its reason, and every failure as prominently as every fix"),
     "the finish summary's contents"),
    (("exactly one LEDGER.md entry is appended and the finish summary gives the tally: pending "
     "at start, completed, already done, failed with reasons, remaining"), "a LEDGER entry"),
    (("LEDGER.md's byte size is MEASURED this run and stated, and if it was over the cap the "
     "rotation ran this run (with its archive path) rather than being deferred"),
     "a LEDGER entry"),
    (("the finish summary carries the fleet-wide usage_error + missing share of util calls "
     "beside its value at the previous run"), "the finish summary's contents"),
    ("state/phase.json is advanced to steady", "a phase write"),
    ("the review cursor is moved past every util this run read", "a cursor advance"),
    ("one WORKLOG entry is appended for the run", "a work-log entry"),
    ("die Abschlusszusammenfassung trennt **Process:** und **Work:** und sagt, was neu einging",
     "the finish summary's contents"),
]


@pytest.mark.parametrize(("outcome", "label"), PROCESS_ONLY)
def test_a_line_about_only_the_runs_record_is_named(outcome, label):
    assert taskcontract.process_only(outcome) == label


TASK_LINES = [
    "the watchlist file holds every verified model with the probe that verified it",
    ("every upcoming ride whose key is not in state/processed_rides.json was found, or the search "
    "established that none is new"),
    "every due source was read, or recorded as unreadable",
    "the export ran with its summary named, or its error recorded verbatim",
    ("config, experiment ledger, dataset manifest and model registry let the next run resume "
    "cleanly"),
    ("on a run that evaluated a checkpoint, its FRESH-ARBITRARY-INPUT score is in the ledger row "
    "beside it"),
    ("the nearest worthwhile deadline without an application got one, waiting for his sign-off, "
    "or the summary says why none was due"),
]


@pytest.mark.parametrize("outcome", TASK_LINES)
def test_a_product_line_is_never_process_only(outcome):
    assert taskcontract.process_only(outcome) is None


def test_a_mixed_line_names_its_record_without_being_process_only():
    """The task clause keeps the line; the record clause is what the note asks to drop."""
    line = ("each delivered ride's key is in state/processed_rides.json and LEDGER.md has one "
            "entry per ride, or one saying no ride was new")
    assert taskcontract.process_only(line) is None
    assert taskcontract.names_a_record(line) == "a LEDGER entry"


def test_a_product_ledger_is_not_the_runs_ledger():
    """Lower-case "the ledger" is as often a product record (an evaluation ledger) — only the
    file's capitalised name and a "ledger entry" count."""
    assert taskcontract.names_a_record("the score is in the ledger row beside its checkpoint") is None
    assert taskcontract.names_a_record("the experiment ledger holds every run's metrics") is None


def test_the_outcome_contract_line_is_the_task_by_definition():
    """The engine generates it (`outcome_words.done_when_line`) and the setup surface BLOCKS
    without it, so its mention of the summary is never a contract defect."""
    cfg = OutcomeConfig.model_validate({"principal": "Mark", "measures": [
        {"id": "syncs_pushed", "kind": "count", "better": "higher", "label": "syncs pushed"}]})
    line = outcome_words.done_when_line(cfg)
    assert taskcontract.process_only(line) is None
    assert taskcontract.names_a_record(line) is None


# ---- a sentence telling the run to write or rotate the ledger ---------------------------------

WRITES = [
    "Append exactly one LEDGER entry: the tax year, the three filings and their tickets.",
    "**Rotate `LEDGER.md` when it exceeds 100 KB**, in this run, as part of recording.",
    "Record accepted **and rejected** candidates in the LEDGER, with the reason for each.",
    "- d6 · record — state/ and the LEDGER say what changed, why, and what the next run picks up",
    "Its `next` line is the handover.\n- **`LEDGER.md`** — exactly one entry: what advanced.",
]


@pytest.mark.parametrize("text", WRITES)
def test_a_ledger_write_instruction_is_found(text):
    assert taskcontract.ledger_instructions(text)


READS = [
    "Read the LEDGER tail the digest carries before anything new.",
    "Record each answer through the ledger tool (`bin/ledger.py set asks <id> state=…`).",
    ("Never print, echo, log or quote its contents anywhere — not in state files, not in the "
    "LEDGER, not in the finish report."),
    "Record the regression debt in the ledger row beside the checkpoint.",
]


@pytest.mark.parametrize("text", READS)
def test_reading_the_ledger_or_a_product_ledger_is_no_instruction(text):
    assert taskcontract.ledger_instructions(text) == []


def test_a_wrapped_sentence_is_read_whole_and_located():
    text = "# record\n\nWhen the run is done, append\none short entry to the `LEDGER.md` file.\n"
    assert taskcontract.ledger_instructions(text) == [
        (3, "When the run is done, append one short entry to the `LEDGER.md` file.")]


# ---- the library patterns ---------------------------------------------------------------------

PATTERN = '''"""A pattern."""
from routine.actions import read_file, finish
from routine.state import phase, ledger
META = {"name": "P", "slug": "p", "description": "d", "when_to_use": "w", "version": 1,
        "tags": ["a", "b", "c"], "includes": [], "tools": None}
PHASES = ["steady"]
DONE_WHEN = ["d1 · work — the page is published and read back",
             "d2 · record — the LEDGER entry names what changed"]
def main():
    """Do the work."""
'''


def test_a_pattern_importing_the_ledger_helper_fails_lint():
    found = lint_workflow_py(PATTERN, filename="p.py", rule_slugs=[])
    assert any("imports `ledger`" in p for p in found)
    assert any("DONE_WHEN d2 names a LEDGER entry" in p for p in found)
    assert not any("DONE_WHEN d1" in p for p in found)


def test_a_harness_owes_no_done_when_but_still_no_bookkeeping():
    src = PATTERN.replace('"tags": ["a", "b", "c"]', '"tags": ["meta", "b", "c"]')
    found = taskcontract.pattern_problems(src, parse_py(src), "p.py")
    assert any("imports `ledger`" in p for p in found)
    assert not any("DONE_WHEN" in p for p in found)


def test_a_clean_pattern_passes():
    src = (PATTERN.replace("from routine.state import phase, ledger\n", "")
           .replace(',\n             "d2 · record — the LEDGER entry names what changed"', ""))
    assert taskcontract.pattern_problems(src, parse_py(src), "p.py") == []


@pytest.mark.parametrize("path", sorted(SEED.glob("*.py")), ids=lambda p: p.stem)
def test_every_seed_pattern_names_only_the_task(path):
    src = path.read_text(encoding="utf-8")
    assert taskcontract.pattern_problems(src, parse_py(src), path.name) == []


# ---- the cause: both generation prompts carry the rule ----------------------------------------

def test_the_pattern_format_names_no_ledger_helper_and_carries_the_contract():
    assert "ledger" not in generate.FORMAT_SPEC.split("META = {")[0]
    assert taskcontract.PATTERN_CONTRACT in generate.FORMAT_SPEC


def test_every_materialization_call_carries_the_recipe_contract():
    """SELF_CONTAINED rides the main call and every stage call; the outline is told no stage
    records the run."""
    assert taskcontract.RECIPE_CONTRACT in pipeline_prompts.SELF_CONTAINED
    assert "recording the run" in pipeline_prompts.OUTLINE_TAIL
    assert "never of the run's own record" in pipeline_prompts.MAIN_RULES
