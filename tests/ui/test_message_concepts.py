"""A message element's JSON is LIT, and the concepts it names are LINKS.

Operator, 2026-09-30: *"i want the action JSON in a message element to be syntax highlighted.
also if reminders or rules fire it needs to be nicely highlighted, formatted, and link to the
specific rule or reminder. in general i want all concepts in the message elements to by
hyperlinked to their respective item: utils, permissions, rules, reminders, anything"*

Two properties matter and both are asserted against the real renderer in the real browser:

1. The action-json fold is highlighted AND still copies back as the exact source. A highlighter
   that dropped or re-ordered a character would make the fold useless for its actual purpose —
   a reader goes there to reproduce a call — so the text content is compared to the JSON the
   fixture put in, byte for byte.
2. A concept is linked from the action's own STRUCTURED FIELD and nowhere else. The util link
   must point at that util's library page, and the args beside it must NOT be swept into the
   anchor; the rule link must point at the rule's page. `read_rule name=list` is the catalog,
   not a rule, so it must stay unlinked — the one negative case that keeps the rule from being
   "link anything that looks like a name".
"""
import json

from playwright.sync_api import expect

_FIXTURE = """async (actions) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'concepts'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const payload of actions) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-09-30T00:00:00Z', payload});
  }
  return box.querySelectorAll('.turn').length;
}"""


def _actions():
    return [
        {"kind": "util", "name": "websearch", "args": ["quantum", "--json"],
         "say": "searching"},
        {"kind": "read_rule", "name": "fix-the-cause", "say": "reading the rule"},
        {"kind": "read_rule", "name": "list", "say": "the catalog"},
    ]


def test_the_action_json_is_highlighted_and_still_the_exact_source(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    actions = _actions()
    assert ui_page.evaluate(_FIXTURE, actions) == len(actions)

    box = ui_page.locator("#concepts")
    fold = box.locator("details.raw pre.hl-json").nth(0)
    # highlighted: the tokenizer emitted classed spans for keys, strings and punctuation
    assert fold.locator(".tok-key").count() > 0
    assert fold.locator(".tok-str").count() > 0
    assert fold.locator(".tok-punc").count() > 0
    # and lossless: the fold's text is still exactly what JSON.stringify produced.
    # `text_content()` and not `inner_text()`: the fold is a COLLAPSED <details>, and
    # inner_text() answers "" for anything not rendered — which would read as a highlighter
    # that ate the source rather than as a closed disclosure widget.
    expected = json.dumps(actions[0], indent=1)
    assert fold.text_content() == expected
    assert json.loads(fold.text_content()) == actions[0]


def test_a_named_concept_links_to_its_page_and_a_catalog_read_does_not(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    actions = _actions()
    assert ui_page.evaluate(_FIXTURE, actions) == len(actions)

    rows = ui_page.locator("#concepts .turn")
    # 1 · the util's name is a link to its library entry; its args are NOT inside the anchor
    util_link = rows.nth(0).locator(".act a.concept-link")
    expect(util_link).to_have_attribute("href", "#/library/util/websearch")
    assert util_link.inner_text() == "websearch"
    assert "quantum" in rows.nth(0).locator(".act").inner_text()

    # 2 · a rule read links to the rule's own prose
    rule_link = rows.nth(1).locator(".act a.concept-link")
    expect(rule_link).to_have_attribute("href", "#/library/rule/fix-the-cause")

    # 3 · the negative case: `list` is the catalog, not a rule — nothing to link to
    assert rows.nth(2).locator(".act a.concept-link").count() == 0
