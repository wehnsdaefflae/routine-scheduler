"""The Decisions page's PROPOSALS group, driven in a real browser (R2425).

The page is an inbox sorted by urgency, and every group caption promised a CONSUMER waiting on
the operator. For a standing proposal — a routine filing an idea it had, a config patch it
noticed — "Deferred — the next run picks these up" is literally false: the filing run has
ended and nothing picks the answer up on a clock. Those items now land in their own group,
below everything that waits, with no urgency affordance at all.

What is asserted here is what a person SEES on the page:

* the group exists, with the caption in the operator's own words;
* it renders BELOW the consumer groups and ABOVE Settled;
* the card carries NO expiring chip, NO relative age (`.rel` / `[data-when]`, which the
  console's time ticker rewrites every 30 s into "3w ago" — lateness, on an item that cannot
  be late) and no countdown;
* the count is not in the console's coral "waiting on a person" colour (`.q-group-count` keeps
  its `settled` modifier, the same muted treatment Settled gets);
* it has its own filter chip, like every other group.

The predicate is DERIVED from what /api/questions already returns — a deferred routine
question whose asking run has ENDED (or whose run dir is gone, so the row carries no
`run_state`). Nothing was added to the question record, so a proposal is seeded here exactly
as the engine files one: a pending record whose `asked` names no live run.
"""

from playwright.sync_api import expect

CAPTION = "considered ideas, decide when you like"
HEADS = ".q-group-head"


def test_a_standing_proposal_gets_its_own_group_under_the_ones_that_wait(ui, ui_page):
    """Three items, three groups, in urgency order: a live blocking ask, a deferred ask whose
    run is still going, and a proposal whose run has ended. The proposal's caption promises no
    consumer, and it sits last of the three."""
    # 1. blocking: a parked run. It must be the routine's NEWEST run — registry.active_run
    # only ever looks at the last one, so an even newer run here would leave this record with
    # no live run behind it, which decisions_read downgrades to `deferred`.
    ui.seed_run("uir", "20260715-110000", "waiting_user",
                question={"qid": "q-blk", "question": "Overwrite the export?", "options": [],
                          "default": "keep both", "asked": "20260715-110000"})
    ui.seed_question("uir", "q-blk", "Overwrite the export?", mode="blocking",
                     default="keep both", asked="20260715-110000")
    # 2. deferred with a LIVE run behind it — a consumer really is coming
    ui.seed_run("uir", "20260715-100000", "running")
    ui.seed_question("uir", "q-live", "Which exporter next?", asked="20260715-100000")
    # 3. a standing proposal: the filing run is over, so nobody is waiting
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-prop", "Rename fau's two ladder keys?", asked="20260714-070000")

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(3)
    expect(heads.nth(0)).to_contain_text("Blocking")
    expect(heads.nth(1)).to_contain_text("Deferred")
    # the group, its noun and the operator's own caption — last, under both that wait
    expect(heads.nth(2)).to_contain_text("Proposals")
    expect(heads.nth(2)).to_contain_text(CAPTION)
    # and the item under it is the proposal, not the live-run ask
    expect(ui_page.locator(".question-item", has_text="ladder keys")).to_be_visible()


def test_the_proposals_group_renders_above_settled(ui, ui_page):
    """Settled stays the bottom of the page: a proposal is still OPEN, so it cannot sort below
    what is already answered."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-prop", "Rename fau's two ladder keys?", asked="20260714-070000")
    ui.seed_question("uir", "q-done", "Already decided?", asked="20260714-070000")
    answers = ui.routine_dir("uir") / "inbox"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / "answer-q-done.json").write_text('{"qid": "q-done", "text": "yes", '
                                                '"source": "web"}', encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(2)
    expect(heads.nth(0)).to_contain_text("Proposals")
    expect(heads.nth(1)).to_contain_text("Settled")


def test_a_proposal_card_shows_no_urgency_affordance(ui, ui_page):
    """The point of the group. A proposal that has sat three weeks is not late, so the card
    carries no expiring chip and no relative age — the `.rel` span the time ticker rewrites
    into "3w ago" is absent, and the filing date is there instead. Absent, not merely
    invisible: a muted countdown is still a countdown."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-prop", "Rename fau's two ladder keys?", asked="20260714-070000")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="ladder keys")
    expect(card).to_be_visible()
    expect(card.locator(".chip", has_text="expiring")).to_have_count(0)
    expect(card.locator("[data-when]")).to_have_count(0)      # nothing the ticker ages
    expect(card.locator(".when .rel")).to_have_count(0)
    expect(card).to_contain_text("filed")                      # the flat date, not an age
    expect(card).not_to_contain_text("ago")
    # the count is NOT the console's coral "someone is waiting" colour
    expect(ui_page.locator(f"{HEADS} .q-group-count.settled")).to_have_count(1)


def test_a_waiting_deferred_card_still_shows_its_age(ui, ui_page):
    """The control for the test above: the affordance is switched off for proposals only, not
    removed from the page. A deferred ask with a live run keeps its relative age."""
    ui.seed_run("uir", "20260715-100000", "running")
    ui.seed_question("uir", "q-live", "Which exporter next?", asked="20260715-100000")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="exporter")
    expect(card).to_contain_text("asked")
    expect(card.locator("[data-when]")).to_have_count(1)


def test_the_predicate_is_derived_from_the_payload_the_api_already_sends(ui, ui_page):
    """`waitsOnYou` asked directly, for the rows the page cannot easily seed: a conversation's
    question and a detached task's question are read when that surface resumes, so they are
    never proposals however old their run is. Blocking and meta always wait.

    This also pins what the derivation CANNOT see: a routine's deferred ask whose run has
    ended reads as a proposal even if that routine is scheduled to run in an hour — the
    payload carries no schedule. That is the honest limit of deriving it client-side, and it
    is asserted rather than left to be rediscovered.
    """
    ui_page.goto(f"{ui.url}/#/questions")
    verdicts = ui_page.evaluate(
        "async () => {"
        " const m = await import('/static/views/questions-taxonomy.js');"
        " const w = m.waitsOnYou;"
        " return {"
        "  blocking: w({ mode: 'blocking' }),"
        "  meta: w({ mode: 'deferred', meta: true }),"
        "  conversation: w({ mode: 'deferred', conversation: true, run_state: 'finished' }),"
        "  background: w({ mode: 'deferred', background: true, run_state: 'finished' }),"
        "  live_run: w({ mode: 'deferred', run_state: 'running' }),"
        "  waiting_run: w({ mode: 'deferred', run_state: 'waiting_user' }),"
        "  ended_run: w({ mode: 'deferred', run_state: 'finished' }),"
        "  no_run_at_all: w({ mode: 'deferred' }),"
        "  kind_of_proposal: m.kindOf({ mode: 'deferred', run_state: 'failed' }),"
        "  kind_of_deferred: m.kindOf({ mode: 'deferred', run_state: 'running' }) }; }")
    assert verdicts == {
        "blocking": True, "meta": True, "conversation": True, "background": True,
        "live_run": True, "waiting_run": True,
        "ended_run": False,          # the filing run is over — nobody is waiting
        "no_run_at_all": False,      # the run dir is gone; there is no consumer to name
        "kind_of_proposal": "proposal", "kind_of_deferred": "deferred"}


def test_proposals_have_their_own_filter_chip(ui, ui_page):
    """Filterable like every other group — and its items are NOT counted into Deferred, which
    is the half that makes the count honest."""
    ui.seed_run("uir", "20260715-100000", "running")
    ui.seed_question("uir", "q-live", "Which exporter next?", asked="20260715-100000")
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-prop", "Rename fau's two ladder keys?", asked="20260714-070000")

    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.get_by_role("button", name="Deferred · 1")).to_be_visible()
    chip = ui_page.get_by_role("button", name="Proposals · 1")
    expect(chip).to_be_visible()
    chip.click()
    expect(ui_page.locator(".question-item")).to_have_count(1)
    expect(ui_page.locator(".question-item")).to_contain_text("ladder keys")
