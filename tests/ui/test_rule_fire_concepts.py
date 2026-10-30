"""A rule FIRE reads as a fire, and a reminder id in a side field links to its row.

F591's two remaining increments (increments 1 and 3 shipped in 18d07e0). Operator, 2026-09-30:
*"also if reminders or rules fire it needs to be nicely highlighted, formatted, and link to the
specific rule or reminder"*.

Both properties are asserted against the real renderer in the real browser:

1. **A reminder id is a LINK when the mount knows whose transcript it is.** `createTranscript`
   takes the routine `slug`, so a `rem-…` in a `remind` / `remind_feedback` side field points at
   `#/changes/<slug>`, whose Recipe health carries that reminder's row, its pattern and its tally
   (`static/views/routine-health.js`). Without a slug there is nowhere to send the reader, so the
   id stays the plain `<code>` it already was — a dead link is worse than dead text, and that
   negative case is asserted too, because it is the one a later "link everything" change breaks.

2. **A rule FIRE is not an ordinary result.** A rule that HELD an action, or that fired on one's
   result, names itself in the payload's `assists` as `<rule>/<id>` (engine/assist.py `hold` and
   `at_observation`), and that field is the ONLY place the slug is read from: the house rule for
   this work is LINK FROM STRUCTURED FIELDS, NEVER FROM FREE TEXT (components/conceptlinks.js —
   a rule called `work-order` would match ordinary English in every say line on the instance).
   The strip sits OUTSIDE the collapsed result fold, because a reader has to see that a rule
   fired without expanding anything, and the row keeps the `obs-held` state word the 2026-09-28
   held/refused/usage vocabulary gave it. An observation carrying no `assists` gets NO strip: a
   row never states something its payload does not say.
"""
from playwright.sync_api import expect

# The real renderer, mounted twice — once knowing the routine, once not. The box id says which,
# so one fixture serves both halves of property 1.
_FIXTURE = """async ([slug, events]) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div');
  box.id = slug ? 'with-slug' : 'no-slug';
  document.body.append(box);
  const opts = {isLive: () => true};
  if (slug) opts.slug = slug;
  const t = createTranscript(box, opts);
  for (const ev of events) t.add(ev);
  return box.querySelectorAll('.turn').length;
}"""

# A revise carries the id of the reminder it rewrites, and the feedback label names the same one:
# two side fields, two ids, both of them a field of their own rather than prose to be scanned.
_REMIND_TURN = {
    "type": "assistant_action", "turn": 1, "ts": "2026-10-30T00:00:00Z",
    "payload": {"kind": "util", "name": "fs-ops", "args": ["mv", "a", "b"], "say": "moving it",
                "remind": {"op": "revise", "id": "rem-mv", "regex": "^util:fs-ops mv ",
                           "description": "it overwrites the destination", "scope": "local"},
                "remind_feedback": {"id": "rem-mv", "label": "would_have"}},
}


def _turn(n):
    return {"type": "assistant_action", "turn": n, "ts": "2026-10-30T00:00:00Z",
            "payload": {"kind": "util", "name": "example", "say": "a fixture turn"}}


def _obs(n, payload):
    return {"type": "observation", "turn": n, "ts": "2026-10-30T00:00:01Z", "payload": payload}


# Three rows: a rule that HELD the action, a rule that fired on a RESULT, and a result no rule
# touched. The third is the negative case that keeps the strip from becoming decoration.
_HELD = {"kind": "assist_hold", "action": "write_file path=/repo/x",
         "lines": [("[RULE git-checkpoint — a tree with uncommitted work] commit a checkpoint "
                    "before the first edit (the full rule: read_rule name=git-checkpoint)")],
         "assists": ["git-checkpoint/undo-point"]}
_FIRED = {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok",
          "assists": ["verify-independently/read-it-back"]}
_QUIET = {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok"}

_RULE_EVENTS = [_turn(1), _obs(1, _HELD), _turn(2), _obs(2, _FIRED), _turn(3), _obs(3, _QUIET)]


def test_a_reminder_id_links_to_its_row_only_when_the_mount_knows_the_routine(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    assert ui_page.evaluate(_FIXTURE, ["uir", [_REMIND_TURN]]) == 1
    assert ui_page.evaluate(_FIXTURE, [None, [_REMIND_TURN]]) == 1

    linked = ui_page.locator("#with-slug .note a.concept-link")
    # both side fields name the reminder, so both of them link to it
    assert linked.count() == 2, "the remind and remind_feedback ids should both be links"
    expect(linked.first).to_have_attribute("href", "#/changes/uir")
    assert linked.first.inner_text() == "rem-mv"
    expect(linked.first).to_be_visible()
    # the link REPLACES dead text and nothing else: the pattern, the consequence and the label
    # still read beside it, as prose, exactly as they did before
    with_slug = ui_page.locator("#with-slug").text_content()
    assert "^util:fs-ops mv " in with_slug
    assert "it overwrites the destination" in with_slug
    assert "would_have" in with_slug

    # No slug: no REMINDER pretends to be clickable. `#/changes/undefined` is the failure this
    # asserts against — a link that lands on a routine page for a routine of that name.
    #
    # Scoped to `.note`, the side-field rows, and not to the whole turn: the `.act` line's
    # util-name link (increment 3, shipped in 18d07e0) is a `concept-link` too and is CORRECT
    # here — a util's page needs no routine to point at. An unscoped count asserts that
    # increment away, which is how this assertion first failed against working code.
    plain = ui_page.locator("#no-slug")
    assert plain.locator(".note a.concept-link").count() == 0
    assert plain.locator(".act a.concept-link").count() == 1    # the util name still links
    assert plain.locator("code.rem-id").count() == 2
    assert "rem-mv" in plain.text_content()
    assert "undefined" not in plain.text_content()


def test_a_rule_fire_names_its_rule_as_a_link_outside_the_collapsed_fold(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    assert ui_page.evaluate(_FIXTURE, ["uir", _RULE_EVENTS]) == len(_RULE_EVENTS) / 2

    box = ui_page.locator("#with-slug")
    strips = box.locator("[data-rule-fire]")
    # exactly the two rows whose payload names a rule — the third is left alone
    assert strips.count() == 2, "a fire strip belongs to every row whose payload names a rule"

    # 1 · the rule that HELD the action links to its own prose, from `assists` and nowhere else
    held_link = strips.nth(0).locator("a.concept-link")
    expect(held_link).to_have_attribute("href", "#/library/rule/git-checkpoint")
    assert held_link.inner_text() == "git-checkpoint"
    # the assist's own id rides along, so a rule with several declarations says WHICH fired
    assert "undo-point" in strips.nth(0).inner_text()

    # 2 · it is READABLE WITHOUT A CLICK: the strip is visible while the result fold beside it
    #     is still collapsed. A strip rendered inside the fold would satisfy every assertion
    #     about hrefs above and still leave the fire invisible, which is the actual complaint.
    expect(strips.nth(0)).to_be_visible()
    turn = box.locator(".turn").nth(0)
    fold = turn.locator("details.obs-collapse")
    assert fold.count() == 1
    assert fold.evaluate("(d) => d.open") is False
    assert turn.locator("details [data-rule-fire]").count() == 0

    # 3 · the held row keeps the state word the vocabulary gives it (operator, 2026-09-28)
    expect(fold).to_have_class("obs-collapse obs-held")

    # 4 · a rule that fired on a RESULT says so on that result's row — the same strip, the same
    #     link, and the row's own state is untouched (this one succeeded)
    fired_link = strips.nth(1).locator("a.concept-link")
    expect(fired_link).to_have_attribute("href", "#/library/rule/verify-independently")
    expect(box.locator(".turn").nth(1).locator("details.obs-collapse")).to_have_class(
        "obs-collapse obs-ok")

    # 5 · the negative case: no `assists` in the payload, no strip, nothing invented
    assert box.locator(".turn").nth(2).locator("[data-rule-fire]").count() == 0
