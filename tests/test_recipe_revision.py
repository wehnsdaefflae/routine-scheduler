"""D200 — the finish seam asks every finishing run for its own RECIPE REVISIONS.

The operator's requirement (2026-10-19): *"In the future I want recipes to suggest a change in
their recipes themselves when they finish."* His answer to where it lives: **the engine finish
seam — every finishing run is asked, like D195's known-flaw prompt** — over a general rule (which
reaches only routines whose config holds it) and over per-recipe text.

Two halves, and both are pinned here because either alone is useless:

1. **A finishing run IS ASKED** — the ask is on the prompt surface of every routine run, in the
   digest's FINISH block (`engine/finish_digest.py`), which is the never-evicted system message
   and the block that already says what a finish owes. Not in the harness contract: that body is
   the always-on ENGINE CONTRACT held to a shrink-only budget (`tests/test_prompt_budget.py`),
   where new prose is paid for by cutting prose.
2. **What it answers LANDS where the operator can see it** — the constraint the decision row
   records is that a scheduled run holds no `write_recipe` (vodafone-outage-claims' own d17 asked
   this of itself on 2026-10-20 and the engine refused the edit), so the lines become ONE
   standing proposal on the Decisions page (`rsched/pending.py`, the surface that already shows
   what a run proposes). Approving it ARMS the revise leg that already exists
   (`engine/revise.py`): the proposing run is resumed with its recipe writable and makes the
   edit itself — the "grant the capability at that moment" half of the same constraint.

A run that ALREADY holds recipe self-write is asked the same question and files no card: it can
make the edit, so a proposal would duplicate it. That branch is pinned too — the seam must not
break or duplicate self-editing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import finish, make_test_server
from helpers import run_context, server_config
from rsched import pending
from rsched.engine import recipeproposal
from rsched.engine.composer import build_system_prompt, state_digest

MARKER = recipeproposal.MARKER


def _prompt(routine_dir: Path, tmp_path: Path, **digest_kwargs) -> str:
    ctx = run_context(routine_dir, "20260712-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
    digest = state_digest(routine_dir, [], [], **digest_kwargs)
    return build_system_prompt(ctx, "## Run flow", "task", digest, [])


# ---- half one: every finishing run is ASKED ------------------------------------------------

def test_the_finish_seam_asks_a_finishing_run_for_its_own_recipe_revisions(make_routine,
                                                                          tmp_path):
    """The ask is on the composed prompt surface of an ordinary routine run — no capability, no
    held rule and no recipe text required, which is the whole point of the operator's answer."""
    text = _prompt(make_routine(slug="asked"), tmp_path)
    assert "RECIPE REVISIONS:" in text, (
        "the composed prompt no longer asks a finishing run for its own recipe revisions "
        "(D200) — a recipe cannot ask this of itself, which is why it is the engine's")
    assert MARKER in text, "the ask no longer names the line prefix a proposal is read from"


def test_the_ask_is_not_in_the_shrink_only_engine_contract(make_routine, tmp_path):
    """It rides the DIGEST, not the harness/schema/example contract — that body is re-sent every
    turn of every run and is budget-capped, so this placement is load-bearing, not incidental.
    `test_prompt_budget.py` is the other side of the same fact."""
    from rsched.engine.harness import harness_contract

    d = make_routine(slug="budgeted")
    ctx = run_context(d, "20260712-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
    assert "RECIPE REVISIONS:" not in harness_contract(ctx, kinds=["read_file", "finish"])


def test_a_run_that_may_write_its_recipe_is_told_to_make_the_edit(make_routine, tmp_path):
    """Both wordings exist because a run told to PROPOSE a change it may simply make would route
    around its own capability, and a run told to EDIT what the engine will refuse to write is
    told a falsehood it will obey (F165, one layer along)."""
    d = make_routine(slug="unlocked")
    locked = _prompt(d, tmp_path)
    unlocked = _prompt(d, tmp_path, recipe_unlocked=True)
    assert "it IS writable to you this run" in unlocked and MARKER not in unlocked
    assert "no recipe-writing capability" in locked and MARKER in locked


def test_a_conversation_is_not_asked(make_routine, tmp_path):
    """A conversation's "recipe" is the shared converse pattern, not its own document — it has
    no recipe to revise, so the ask would be an instruction it cannot act on."""
    d = make_routine(slug="conv")
    assert "RECIPE REVISIONS:" not in _prompt(d, tmp_path, conversation=True)


# ---- reading the answer ---------------------------------------------------------------------

@pytest.mark.parametrize(("summary", "expected"), [
    (f"Done.\n{MARKER} stages/scan.md — read the cursor before the feed.",
     ["stages/scan.md — read the cursor before the feed."]),
    (f"Done.\n- {MARKER} main.md — drop step 4.", ["main.md — drop step 4."]),      # in a list
    ("Done. Nothing to add.", []),                                                  # silence
    (f"I thought about a {MARKER.lower()} but did not make one.", []),              # not shouted
    ("The next run should consider a RECIPE REVISION eventually.", []),             # no colon
    (f"{MARKER}   ", []),                                                           # empty line
])
def test_the_lines_a_summary_carries_are_read_and_nothing_else_is(summary, expected):
    assert recipeproposal.extract(summary) == expected


def test_more_revisions_than_one_card_can_show_are_capped():
    summary = "\n".join(f"{MARKER} change {i}" for i in range(20))
    assert len(recipeproposal.extract(summary)) == recipeproposal.MAX_LINES


# ---- half two: the proposal LANDS where the operator sees it --------------------------------

def test_a_finishing_run_files_one_proposal_on_the_decisions_page(make_routine, scripted,
                                                                 tmp_path):
    """End to end through the real engine: the run names a revision in its summary and the
    operator has a card for it, with the run's own words on it."""
    from rsched.engine.runtime import run_routine
    from test_loop import TS, probe
    from test_loop import _server as loop_server

    d = make_routine(slug="proposer")
    server = loop_server(d)
    scripted([probe(), finish(summary="Scanned 38 postings.\n"
                                     f"{MARKER} stages/scan.md — read state/cursor.json before "
                                     "the feed; this run spent two turns re-deriving it.")])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"

    queued = pending.load_all(server.routines_home)
    assert [r["kind"] for r in queued] == [recipeproposal.KIND]
    assert queued[0]["routine"] == "proposer" and queued[0]["run_id"] == f"proposer:{TS}"
    assert queued[0]["fields"]["lines"] == [
        ("stages/scan.md — read state/cursor.json before the feed; this run spent two turns "
         "re-deriving it.")]
    assert "proposes a revision to its own recipe" in queued[0]["summary"]
    # and the run's own result says the card exists, so the next run does not re-propose blind
    assert "Recipe revision(s) proposed to the operator" in (run_dir / "result.md").read_text(
        encoding="utf-8")


def test_a_run_that_says_nothing_files_nothing(make_routine, scripted, tmp_path):
    from rsched.engine.runtime import run_routine
    from test_loop import TS, probe
    from test_loop import _server as loop_server

    d = make_routine(slug="quiet")
    server = loop_server(d)
    scripted([probe(), finish(summary="Scanned 38 postings. The recipe served this run.")])
    assert run_routine(d, server, run_ts=TS)[0] == "ok"
    assert pending.load_all(server.routines_home) == []


def _ctx(routine_dir: Path, server, *, depth: int = 0, unlocked: bool = False):
    return SimpleNamespace(
        depth=depth, server=server, run_id=f"{routine_dir.name}:1",
        routine=SimpleNamespace(dir=routine_dir, slug=routine_dir.name, name="A routine"),
        grants=SimpleNamespace(recipe_unlocked=unlocked),
        transcript=SimpleNamespace(event=lambda *a, **k: None))


def test_a_run_that_may_write_its_own_recipe_files_no_proposal(make_routine, tmp_path):
    """The seam must not DUPLICATE self-editing: a run holding recipe-authoring (or a revise
    leg) edits the file and the autocommit carries it, so a card proposing the same change
    would be a second copy of work already done."""
    d = make_routine(slug="selfedit")
    server = make_test_server(tmp_path)
    assert recipeproposal.maybe_propose(
        _ctx(d, server, unlocked=True), f"Done.\n{MARKER} main.md — tighten step 2.") == ""
    assert pending.load_all(server.routines_home) == []


def test_a_child_run_proposes_nothing(make_routine, tmp_path):
    """A subrun's task is its parent's brief; it has no recipe of its own to revise."""
    d = make_routine(slug="childish")
    server = make_test_server(tmp_path)
    assert recipeproposal.maybe_propose(
        _ctx(d, server, depth=1), f"Done.\n{MARKER} main.md — x.") == ""
    assert pending.load_all(server.routines_home) == []


def test_the_same_revision_twice_is_one_card_and_a_new_one_is_its_own(make_routine, tmp_path):
    """A routine is asked at EVERY finish, so noticing the same gap again must dedupe — and
    genuinely new reasoning must NOT be folded into the standing card and lost
    (`pending.identity`)."""
    d = make_routine(slug="repeat")
    server = make_test_server(tmp_path)
    ctx = _ctx(d, server)
    first = recipeproposal.maybe_propose(ctx, f"{MARKER} main.md — tighten step 2.")
    again = recipeproposal.maybe_propose(ctx, f"{MARKER} main.md — tighten step 2.")
    assert again == first
    assert len(pending.load_all(server.routines_home)) == 1
    recipeproposal.maybe_propose(ctx, f"{MARKER} stages/report.md — drop the table.")
    assert len(pending.load_all(server.routines_home)) == 2


# ---- the doc surface, and what was deliberately NOT changed --------------------------------
# What the operator can DO with the card is `test_recipe_revision_apply.py`.

def test_the_docs_pin_the_ask(make_routine, tmp_path):
    """`docs/prompt-anatomy.md` pins the engine's prose a run reads; a change here without a
    change there is the drift `tests/test_prompt_anatomy.py` exists to fail on."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md").read_text(
        encoding="utf-8")
    assert "RECIPE REVISIONS:" in doc
    fence = doc[doc.index("### 5.1 System prompt"):doc.index("### 5.2")]
    assert "RECIPE REVISIONS:" in fence, (
        "§5.1 quotes a composed prompt without the D200 ask — the example's own routine holds "
        "no recipe-authoring, so the composer emits it there")


def test_nothing_new_was_added_to_the_action_schema(make_routine):
    """The answer rides the SUMMARY, like D195's flaw answer — no new finish field. An
    undeclared one would be silently dropped by `normalize_action`, and a declared one grows
    the budgeted always-on contract in a file at its size ceiling."""
    from rsched.engine.actions import KIND_FIELDS

    assert KIND_FIELDS["finish"] == (("status", "summary"),
                                     ("accounting", "reply_to", "final"))


def test_the_proposal_is_filed_from_the_close_out_every_ending_shares(make_routine, tmp_path):
    """Not from the finish gate: a run that spends its reserved finish turn never reaches the
    gate, and that ending — the budget wall, D195's own second seam — is the one most likely to
    have learned something about its own recipe."""
    import inspect

    from rsched.engine import finishgate, loopend

    assert "recipeproposal" in inspect.getsource(loopend.finish_run)
    assert "recipeproposal" not in inspect.getsource(finishgate)


def test_a_store_failure_never_turns_a_finished_run_into_a_failed_one(make_routine, tmp_path,
                                                                     monkeypatch):
    """Best-effort, in the shape `goalreached` established: the proposal is bookkeeping about a
    run that has already done its work."""
    d = make_routine(slug="broken")
    server = make_test_server(tmp_path)

    def boom(*a, **k):
        raise OSError("the queue is not writable")

    monkeypatch.setattr(pending, "queue", boom)
    assert recipeproposal.maybe_propose(_ctx(d, server), f"{MARKER} main.md — x") == ""
