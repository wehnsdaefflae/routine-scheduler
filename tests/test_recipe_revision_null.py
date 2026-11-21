"""A null recipe revision SCOPED to the recipe files nothing (engine/recipeproposal._is_null)."""

from __future__ import annotations

import pytest

from rsched.engine import recipeproposal

MARKER = recipeproposal.MARKER


@pytest.mark.parametrize("line", [
    # VERBATIM, freelance-hunter 2026-11-15
    ("none needed in main.md/stages/ — the recipe served as written. (The projektagent.py"
     " script addition is exactly the kind of deterministic sub-step persistence the harness"
     " guidance calls for, done in-run rather than proposed.)"),
    "no change to the recipe — every stage held.",
    "no changes needed in stages/scan.md or main.md.",
    "nothing to change in main.md; the stages routed correctly.",
])
def test_a_null_revision_scoped_to_the_recipe_files_nothing(line):
    assert recipeproposal.extract(f"Done.\n{MARKER} {line}") == []


@pytest.mark.parametrize("line", [
    # a scope that is more than files is a gap the recipe has — a proposal
    "nothing in stages/scan.md says which feed is authoritative",
    "no change to stages/scan.md except reading the cursor first",
    # a dot inside a name no longer ends the clause, so the file stays its opening clause
    "v1.2 of main.md — drop step 4.",
    "main.md — drop step 4.",
])
def test_a_real_revision_naming_a_file_is_still_filed(line):
    assert recipeproposal.extract(f"Done.\n{MARKER} {line}") == [line]
