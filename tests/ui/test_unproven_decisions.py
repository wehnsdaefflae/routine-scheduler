"""The Decisions page holds an ANSWERED-BUT-UNPROVEN decision, in a band of its own (D220/F795).

The operator, 2026-10-29: *"You need to come up with a proof at creation and accept / decline my
response based on the result of you checking the proof"* — and the rule that reorganizes the
page: **a decision stays open, visible and open to his feedback until at least one option has a
positive proof, or it is actively discarded or overridden.** F795 states the defect from the
other end: an answered decision leaves this page the moment the answer is recorded, and nothing
ever checks that the authorized work landed.

0.519.0 shipped the mechanism (`rsched/proofs.py`, `rsched/proofcheck.py`) and nothing rendered
it. This is the rendering, and the one constraint it must not break is the HARD one D220 states:
**a row whose proof is unproven must never be re-presented as an unanswered question.** So the
band sits between what waits on a person and what is settled, the card keeps its answer and its
revise control, and nothing counts it as work owed.

What a person SEES, and what is asserted here:

* the band exists, with its own caption, and only for rows that are answered AND unproven;
* a `negative` proof — the actions were performed and the proof is STILL absent, his own
  "unexpected event" state — renders LOUDER than an open question, which is the one place this
  page shouts about something already answered;
* a `positive` proof clears the row into Settled, where an ordinary answered decision goes;
* a decision with NO proof behaves exactly as it always did, which is every row the instance
  has ever had.
"""

from playwright.sync_api import expect

CAPTION = "answered, and nothing yet shows the work landed"
HEADS = ".q-group-head"

_SPEC = {"a: ship it": {"predicate": "release_names_item", "reads": "state/shipped.jsonl",
                        "category": "routine_act"},
         "b: leave it": {"predicate": "item_row_dropped", "reads": "audit/report.json",
                         "category": "routine_act"}}


def _proof(state_a, state_b="declined", **extra):
    """The stored per-option map as `proofs.answered` leaves it, plus any check result."""
    return {"a: ship it": {**_SPEC["a: ship it"], "state": state_a, **extra},
            "b: leave it": {**_SPEC["b: leave it"], "state": state_b}}


def _answer(ui, slug, qid, text="a: ship it"):
    inbox = ui.routine_dir(slug) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"answer-{qid}.json").write_text(
        f'{{"qid": "{qid}", "text": "{text}", "source": "web"}}', encoding="utf-8")


def test_an_answered_but_unproven_decision_stays_on_the_page_in_its_own_band(ui, ui_page):
    """The whole point. He answered; the work it authorized has not been shown to have
    landed; the row is still here, and it says why."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-unproven", "Ship the ladder rename?",
                     options=list(_SPEC), asked="20260714-070000",
                     extra={"proof": _proof("in_progress")})
    _answer(ui, "uir", "q-unproven")

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Unproven")
    expect(heads.nth(0)).to_contain_text(CAPTION)
    card = ui_page.locator(".question-item", has_text="ladder rename")
    expect(card).to_be_visible()
    # it is NOT re-presented as a question: his answer is on the card, with the revise control
    expect(card).to_contain_text("a: ship it")
    expect(card.locator("button", has_text="revise")).to_have_count(1)
    # and the card says what is being checked, so the band is not a mystery
    expect(card).to_contain_text("release_names_item")


def test_a_positive_proof_sends_the_row_to_settled(ui, ui_page):
    """A proof that turned up clears the row — the lifecycle's whole purpose. It lands where
    every other answered decision does."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-proven", "Ship the ladder rename?",
                     options=list(_SPEC), asked="20260714-070000",
                     extra={"proof": _proof("positive")})
    _answer(ui, "uir", "q-proven")

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Settled")


def test_a_negative_proof_says_so_louder_than_an_open_question(ui, ui_page):
    """His `negative` state: the actions were performed and the proof is still absent, which
    flags an unexpected event. It is the one thing on this page that shouts about a decision
    already answered, so the chip is the console's alarm treatment, not the muted one."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-negative", "Ship the ladder rename?",
                     options=list(_SPEC), asked="20260714-070000",
                     extra={"proof": _proof("negative", checked_ts="2026-11-08T15:00:00+01:00")})
    _answer(ui, "uir", "q-negative")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="ladder rename")
    expect(card).to_be_visible()
    # `.chip.failed` is the console's existing alarm treatment — no new CSS class, because both
    # stylesheets are AT their ratchet ceilings and a new rule would have to be paid for in
    # prose somewhere unrelated.
    chip = card.locator(".chip.failed")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text("proof absent")


def test_an_unproven_band_row_is_not_counted_as_work_owed(ui, ui_page):
    """The never-re-ask constraint, measured where it bites: the header badge and the page's
    own open count are what tell him something needs him. An answered row must not be in
    either, however visible it is."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-unproven", "Ship the ladder rename?",
                     options=list(_SPEC), asked="20260714-070000",
                     extra={"proof": _proof("in_progress")})
    _answer(ui, "uir", "q-unproven")

    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(HEADS).nth(0)).to_contain_text("Unproven")
    # the group's own count carries the settled (muted) modifier, not the waiting-on-a-person one
    expect(ui_page.locator(".q-group-count.settled")).to_have_count(1)
    # and nothing anywhere offers it as a question to answer
    expect(ui_page.locator(".question-item .answer-input")).to_have_count(0)


def test_a_decision_with_no_proof_behaves_exactly_as_before(ui, ui_page):
    """Every row the instance has ever had. An answered decision with no proof map goes to
    Settled, as it always did — this mechanism must not re-open the whole history."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-plain", "Already decided?", asked="20260714-070000")
    _answer(ui, "uir", "q-plain", text="yes")

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Settled")
