"""A null recipe revision SCOPED to the recipe files nothing (engine/recipeproposal._is_null),
and — R3810 — is nonetheless RECORDED: a run that answered the ask and a run that never
addressed it must not be byte-identical in the record.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_test_server
from rsched import pending
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


# ---- a null verdict is an ANSWER, and the record says so -----------------------------------

def _recording_ctx(routine_dir: Path, server):
    """A ctx whose transcript KEEPS what was filed, so a test can read the event back."""
    events: list[tuple[str, dict]] = []
    return SimpleNamespace(
        depth=0, server=server, run_id=f"{routine_dir.name}:1", filed=events,
        routine=SimpleNamespace(dir=routine_dir, slug=routine_dir.name, name="A routine"),
        grants=SimpleNamespace(recipe_unlocked=False),
        transcript=SimpleNamespace(event=lambda t, p: events.append((t, p))))


@pytest.mark.parametrize("line", [
    "none.",
    "none — the recipe served this run as written; the stages routed correctly.",
    "no changes needed in stages/scan.md or main.md.",
    "Recipe unchanged: nothing this run contradicted.",
])
def test_a_null_verdict_is_read_as_an_answer_to_the_ask(line):
    """The predicate separates the two cases `extract` cannot tell apart: SAYING there is no
    revision, and saying nothing at all. Both file no card; only the first is an answer.
    """
    assert recipeproposal.null_verdict(f"Done.\n{MARKER} {line}")


@pytest.mark.parametrize("summary", [
    # the ask went unanswered: the expensive case, and the one that must stay distinguishable
    "Scanned 38 postings. The recipe served this run.",
    "",
    # not shouted, so not a verdict; a bare marker answers nothing
    f"I thought about a {MARKER.lower()} but did not make one.",
    f"{MARKER}   ",
    # a real revision is not a null one
    f"Done.\n{MARKER} main.md — drop step 4.",
])
def test_a_summary_that_names_no_verdict_is_not_read_as_one(summary):
    assert not recipeproposal.null_verdict(summary)


def test_an_answered_ask_files_one_event_and_no_card(make_routine, tmp_path):
    """The whole of this item: the distinction the module's own docstring claims to keep had no
    surface at all once the null CARD was removed — `maybe_propose` returned before the event
    was written, so a clean run and a run that ignored the ask left the same empty record.
    """
    d = make_routine(slug="clean")
    server = make_test_server(tmp_path)
    ctx = _recording_ctx(d, server)

    assert recipeproposal.maybe_propose(
        ctx, f"Done.\n{MARKER} none — the recipe served this run as written.") == ""
    assert pending.load_all(server.routines_home) == [], "a null verdict must file NO card"
    assert [t for t, _ in ctx.filed] == ["stopping_update"]
    payload = ctx.filed[0][1]
    assert payload["revisions"] == recipeproposal.NONE
    assert payload["recipe_proposal"] == ""
    assert payload["run_id"] == ctx.run_id


def test_an_unanswered_ask_files_nothing_at_all(make_routine, tmp_path):
    """The other half of the same distinction: silence stays silent, or the event means only
    "a run finished" and counts nothing.
    """
    d = make_routine(slug="silent")
    server = make_test_server(tmp_path)
    ctx = _recording_ctx(d, server)

    assert recipeproposal.maybe_propose(ctx, "Scanned 38 postings. All quiet.") == ""
    assert ctx.filed == []
    assert pending.load_all(server.routines_home) == []


def test_a_real_revision_still_files_its_card_and_names_it_in_the_event(make_routine, tmp_path):
    """A control: recording the null path must not change what a genuine revision does."""
    d = make_routine(slug="proposing")
    server = make_test_server(tmp_path)
    ctx = _recording_ctx(d, server)

    card = recipeproposal.maybe_propose(ctx, f"{MARKER} main.md — drop step 4.")
    assert card
    assert len(pending.load_all(server.routines_home)) == 1
    payload = ctx.filed[0][1]
    assert payload["recipe_proposal"] == card and "revisions" not in payload


def test_both_readers_word_the_null_verdict_as_an_answer():
    """The event rides `stopping_update`, whose two renderers otherwise say "accounting:
    nothing owed" — exactly the wrong sentence for a run that answered a question.
    """
    from rsched.cli_render import _accounting

    payload = {"revisions": recipeproposal.NONE, "recipe_proposal": "", "run_id": "r:1"}
    assert "recipe sound" in _accounting(payload)
    assert "nothing owed" not in _accounting(payload)

    js = (Path(__file__).resolve().parents[1] / "static" / "components"
          / "obs-accounting.js").read_text(encoding="utf-8")
    # The literal, built from the CONSTANT: the console cannot import it, so renaming the token
    # on the Python side would otherwise desynchronise the two readers with nothing failing.
    assert f'p.revisions === "{recipeproposal.NONE}"' in js, (
        "the console's accounting line no longer recognises an answered recipe ask, so a clean "
        "run renders as an accounting that owes nothing")
    assert "recipe sound" in js
