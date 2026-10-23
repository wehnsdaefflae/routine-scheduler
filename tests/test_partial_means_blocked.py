"""D204 — `partial` is for a blocker the run cannot remove, never for a shortfall of its own.

Operator, 2026-10-23, verbatim: *"Routines should finish partially instead of fully okay, only
if they're blocked by sth they cannot unblock themselves. Apply this to you and report it as a
requirement for ALL routines"*.

The engine's own prose said the opposite in four places at once, and the fleet followed it. In
the 14 days to 2026-10-23, 232 runs finished `ok` and 4 `partial` — every one of those four
chosen by the run, none forced by the engine. `routine-improver:20261017-130356` gave "the
budget went to the instrument" as its reason; `self-audit:20261022-223237` finished `partial`
three times while saying nothing had blocked it, citing the harness's "honest partial" line.

The four surfaces, all of which a run reads before it writes its own status:

1. `engine/harness.py` closed the honesty paragraph with *"an honest `partial` beats an
   overclaimed `ok`"* — read beside the sentences that ask a run to name its own flaws, that is
   an instruction to downgrade the status whenever there is a flaw to name. The honesty half is
   the measured D195 intervention and stays; only the status advice changed.
2. `engine/kindprose.py`'s finish bullet defined ok as "everything this run could do is done",
   so any shortfall of the run's own read as not-ok.
3. `engine/actionschema.py`'s `status` description said the same thing in the schema the model
   validates against.
4. `library-seed/workflows/general-task.py` and `converse.py` each named a shortfall of the
   run's own — a missing ledger entry, a mid-plan handover — as `partial`.

The shared rule `evidence-discipline` is the definition all of them must agree with: `partial`
only when something the run cannot unblock itself stopped work it could otherwise have done.
This file holds the engine to that, in both directions — the new meaning is present, and the
two old definitions cannot come back anywhere in the package or the seed library.
"""

import re
from pathlib import Path

from helpers import run_context
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.harness import harness_contract
from rsched.engine.kindprose import KIND_PROSE

REPO = Path(__file__).resolve().parents[1]
DOC = (REPO / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")
SEED = REPO / "library-seed"

#: The two definitions D204 retired. Either one tells a run that its own shortfall is a
#: `partial`, which is exactly the reading the fleet acted on. Narrow on purpose: the `job`
#: action's `blocked` outcome legitimately says "waits on something outside your reach"
#: (`actionschema.py`), and that one already means what D204 wants `partial` to mean.
RETIRED = ("could do is done", "outside your reach stopped feasible")

#: What `partial` now means, in the engine's own words — one phrase per surface.
BLOCKER_HARNESS = "`partial` is for a blocker you cannot remove"
BLOCKER_BULLET = "ONLY when something you cannot unblock yourself stopped work"
BLOCKER_SCHEMA = "only when something you cannot unblock yourself stopped work"


def _finish_bullet() -> str:
    return next(prose for covers, prose in KIND_PROSE if "finish" in covers)


def _status_description() -> str:
    return ACTION_SCHEMA["properties"]["status"]["description"]


def _paragraph(text: str, needle: str) -> str:
    for para in text.split("\n\n"):
        if needle in para:
            return para
    raise AssertionError(f"no paragraph carries {needle!r}")


def test_the_harness_paragraph_no_longer_pairs_a_known_flaw_with_partial(make_routine):
    """The honesty paragraph asks for the flaw and then says what the status is NOT for.

    `self-audit:20261022-223237` quoted the old closing clause as its authority for three
    partial finishes with nothing blocking it — the clause sat two sentences after "name the
    flaw", so naming one read as a reason to downgrade.
    """
    d = make_routine(slug="d204")
    contract = harness_contract(run_context(d, "20261023-130002"),
                               kinds=["read_file", "finish"])
    para = _paragraph(contract, "Name the flaw that would change what a reader concludes")
    assert "an honest `partial` beats an overclaimed `ok`" not in para, (
        "the harness honesty paragraph still tells a run that an honest `partial` beats an "
        "overclaimed `ok` — a run with a flaw of its own to name reads that as the status to "
        "pick (D204)"
    )
    assert BLOCKER_HARNESS in para, (
        "the harness honesty paragraph no longer says what `partial` is for — it must name a "
        "blocker the run cannot remove, so naming a flaw of its own stays an `ok` finish"
    )
    # the D195 half is the measured intervention and must survive the rewrite untouched
    assert "Be honest in your response." in para
    assert "Name the flaw that would change what a reader concludes" in para


def test_the_finish_bullet_does_not_define_ok_as_everything_the_run_could_do(make_routine):
    """`ok` is the default ending, including one with a named shortfall of the run's own."""
    bullet = _finish_bullet()
    for retired in RETIRED:
        assert retired not in bullet, (
            f"the finish bullet still defines the status with {retired!r} — a run that skipped "
            "a step or could not get a check green reads itself as not ok (D204)"
        )
    assert BLOCKER_BULLET in bullet, (
        "the finish bullet no longer restricts `partial` to a blocker the run cannot remove"
    )
    assert "failed (the job could not be done)" in bullet, "the `failed` leg was lost"
    # the bullet is projected per run; the rewrite must still reach a finish-only routine
    d = make_routine(slug="d204b")
    contract = harness_contract(run_context(d, "20261023-130002"), kinds=["finish"])
    assert BLOCKER_BULLET in contract


def test_the_schema_status_description_says_partial_means_blocked():
    """The schema description is the other half of the same sentence: the model validates its
    reply against it, so a definition here that contradicts the bullet is a run told both."""
    desc = _status_description()
    for retired in RETIRED:
        assert retired not in desc, (
            f"ACTION_SCHEMA's `status` description still carries {retired!r} (D204)"
        )
    assert BLOCKER_SCHEMA in desc, (
        "ACTION_SCHEMA's `status` description no longer restricts `partial` to a blocker the "
        "run cannot unblock itself"
    )
    assert "failed = the job could not be done" in desc


def test_no_engine_prose_defines_the_status_by_the_run_s_reach():
    """Both retired definitions are gone from the whole package and the seed library.

    A sweep rather than three assertions: the wording was duplicated across the harness, the
    kind bullet, the schema and two seed patterns, and the next copy will be somewhere else.
    """
    roots = [REPO / "src" / "rsched", SEED]
    hits: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")) + sorted(root.rglob("*.md")):
            # join `\`-continued prose lines FIRST: the engine's prompt strings are written
            # that way, so collapsing whitespace alone leaves a stray `\` between the words
            # and a needle spanning the wrap matches nothing (this check found only
            # actionschema.py on its first run, while kindprose.py carried the same phrase)
            raw = re.sub(r"\\\n\s*", "", path.read_text(encoding="utf-8"))
            text = " ".join(raw.split())
            hits += [f"{path.relative_to(REPO)}: {retired!r}"
                     for retired in RETIRED if retired in text]
    assert not hits, (
        "the retired definitions of `partial` are still in the tree:\n" + "\n".join(hits)
    )


def test_the_doc_quotes_the_engine_s_new_status_prose():
    """docs/prompt-anatomy.md pins copies of all three engine surfaces (§4's explanation and
    §5.1's verbatim fence). A doc still quoting the old wording shows the Help tab a prompt
    the engine does not compose."""
    assert "an honest `partial` beats an overclaimed `ok`" not in DOC, (
        "docs/prompt-anatomy.md still quotes the retired closing clause of the honesty "
        "paragraph — §4's explanation and §5.1's fence both carry it"
    )
    for needle in (BLOCKER_HARNESS, BLOCKER_BULLET, BLOCKER_SCHEMA):
        assert needle in DOC, f"docs/prompt-anatomy.md does not carry the new prose {needle!r}"
    for retired in RETIRED:
        assert retired not in DOC, (
            f"docs/prompt-anatomy.md still quotes {retired!r} as the status definition"
        )


def test_the_seed_general_task_keeps_the_ledger_force_without_the_status_word():
    """The sentence is load-bearing — it is what makes the ledger entry non-optional — so the
    force stays and only the status word goes."""
    text = (SEED / "workflows" / "general-task.py").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "ledger entry is `partial`" not in flat, (
        "the general-task seed still calls a run without its ledger entry `partial` — a "
        "skipped step of the run's own is an `ok` finish with the shortfall named (D204)"
    )
    assert "has failed its record" in flat, (
        "the general-task seed lost the force of its ledger sentence: a run that ends without "
        "its ledger entry must still be told that is a failure of its record"
    )
    assert "append exactly one LEDGER entry" in flat


def test_the_seed_converse_does_not_call_a_mid_plan_handover_partial():
    """A conversation that hands back mid-plan has done what it could; the goals stay open."""
    flat = " ".join((SEED / "workflows" / "converse.py").read_text(encoding="utf-8").split())
    # the needle is the retired CLAUSE, not the words "mid-plan": the pattern still names that
    # handover — to say it is an ordinary `ok`, which is the whole point of the change
    assert "partial when the job is not done" not in flat, (
        "the converse seed still calls a mid-plan handover `partial` — a non-final reply is an "
        "`ok` finish with the goals left open (D204)"
    )
    assert "reserve partial for something you cannot unblock yourself" in flat, (
        "the converse seed no longer says what partial IS for, so a reply with work left over "
        "has nothing to read but the old habit"
    )
    assert "Status ok" in flat, "the converse seed no longer states the reply's status"


def test_the_seed_rule_carries_the_definition_the_engine_now_states():
    """`library-seed/rules/evidence-discipline.md` is where the definition lives for a fresh
    install. The engine's prose agreeing with a rule the seed does not carry would hand a new
    instance the contradiction this change removes."""
    flat = " ".join((SEED / "rules" / "evidence-discipline.md")
                    .read_text(encoding="utf-8").split())
    assert "`partial` only when something you cannot unblock yourself stopped work" in flat, (
        "the seeded evidence-discipline rule still defines the status by the run's reach"
    )
    assert "A shortfall of your own is never a blocker" in flat
    assert re.search(r"a step you skipped,.*work you chose to leave", flat), (
        "the seeded rule no longer lists what counts as a shortfall of the run's own"
    )
