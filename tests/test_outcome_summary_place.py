"""Where the OUTCOME goes in a finish summary — the schema and the outcome-first check agree."""

from __future__ import annotations

import re
from pathlib import Path

from rsched.config.outcome import OutcomeConfig
from rsched.engine import outcome_words, verifier
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.readmodels.surface_outcome import names_a_measure

ROOT = Path(__file__).resolve().parents[1]

CFG = OutcomeConfig.model_validate({
    "principal": "Mark",
    "measures": [{"id": "syncs_pushed", "kind": "count", "better": "higher",
                  "label": "mirror syncs pushed"},
                 {"id": "days_since_push", "kind": "level", "better": "lower",
                  "label": "days since the last push"}],
})


def test_the_generated_done_when_line_puts_the_outcome_where_the_schema_puts_it():
    line = outcome_words.done_when_line(CFG)
    assert "**Work:**" in line
    assert "leads with the outcome" not in line        # the clause two routines paid for
    assert "scripts/outcome.py" in line
    for mid in ("syncs_pushed", "days_since_push"):
        assert names_a_measure(line, [mid]), mid       # the setup surface still finds its measures


def test_the_line_and_the_schema_name_the_same_part():
    """The ONE property F806 is about: the sentence a recipe is given and the sentence the model
    is given must not point at different places in the same summary."""
    described = ACTION_SCHEMA["properties"]["summary"]["description"]
    assert "**Work:**" in described
    assert "**Work:**" in outcome_words.done_when_line(CFG)
    # …and the schema still describes the two-part shape the operator settled on
    assert "TWO labelled parts" in described and "**Process:**" in described


def test_the_claim_judge_is_told_where_the_outcome_belongs():
    """The judge decides these lines. Shown a two-part summary whose counts sit in `**Work:**`,
    it must not read the schema's own shape as the line's failure."""
    claims = [{"id": "d7", "stage": "", "text": outcome_words.done_when_line(CFG)}]
    prompt = verifier._prompt(claims, "**Process:** exported 3 files.\n"
                                      "**Work:** syncs_pushed 1 · days_since_push 0.", "tail")
    assert "**Work:**" in prompt
    assert "a two-part summary" in prompt


def test_no_surface_still_tells_a_routine_to_lead_with_the_outcome_line():
    """The generator is the only author of this clause, so no other shipped surface may keep
    the old wording — a recipe written from a doc would hit the same contradiction."""
    for rel in ("src", "docs", "library-seed", "static"):
        for path in sorted((ROOT / rel).rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".md", ".js"):
                continue
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            hits = [ln for ln in text.splitlines()
                    if re.search(r"summary leads with the outcome", ln)]
            assert not hits, f"{path.relative_to(ROOT)}: {hits}"
