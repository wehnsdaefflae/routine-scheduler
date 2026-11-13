"""reflinks.js turns every item id in maintenance prose into a link — however many digits.

The pattern was capped at four digits while the `R` namespace was about to pass R10000; an id
past the cap stayed plain text. Evaluated in the browser ESM context against the module the
console serves.
"""

from __future__ import annotations


def test_an_item_id_past_four_digits_still_links(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate("""() => import('/static/components/reflinks.js').then((m) => {
        const box = document.createElement('div');
        box.textContent = 'see R10423, F7 and D123456 — not X99';
        m.linkifyRefs(box);
        return { links: [...box.querySelectorAll('a.ref-link')]
                   .map((a) => [a.textContent, a.getAttribute('href')]),
                 text: box.textContent, encoded: m.refHref('R1&x') };
    })""")
    assert out["links"] == [["R10423", "#/messages?focus=R10423"],
                            ["F7", "#/messages?focus=F7"],
                            ["D123456", "#/messages?focus=D123456"]]
    assert out["text"] == "see R10423, F7 and D123456 — not X99"      # nothing else rewritten
    assert out["encoded"] == "#/messages?focus=R1%26x"


def test_a_reference_says_what_the_thing_is_not_only_its_id(ui, ui_page):
    """The operator's requirement, 2026-11-12: *"worst case scenario is content i need to know
    about being referenced only by an identifier"* — so a ref carries the thing's subject.

    The id STAYS in the link text: he names items by id when he answers, and a link whose id
    he cannot read cannot be quoted back.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate("""() => Promise.all([
        import('/static/components/reflinks.js'), import('/static/ref-titles.js'),
    ]).then(([links, store]) => {
        store.__seed({ R4242: 'the Decisions page is an inbox sorted by urgency',
                       F99: 'x'.repeat(200) });
        const box = document.createElement('div');
        box.textContent = 'see R4242, F99 and R7777';
        links.linkifyRefs(box);
        const a = [...box.querySelectorAll('a.ref-link')];
        return { texts: a.map((n) => n.textContent), titles: a.map((n) => n.title),
                 hrefs: a.map((n) => n.getAttribute('href')) };
    })""")
    # the known subject rides the link, after the id
    assert out["texts"][0] == "R4242 — the Decisions page is an inbox sorted by urgency"
    assert out["titles"][0].startswith("R4242: the Decisions page is an inbox")
    # a subject too long for a sentence is clipped in the TEXT and whole in the tooltip
    assert out["texts"][1].startswith("F99 — ") and len(out["texts"][1]) < 90
    assert out["texts"][1].endswith("…")
    assert len(out["titles"][1]) > 200, "the tooltip must still carry the whole subject"
    # an id with no known subject is exactly what it always was — never a dead end
    assert out["texts"][2] == "R7777"
    assert out["hrefs"] == ["#/messages?focus=R4242", "#/messages?focus=F99",
                            "#/messages?focus=R7777"]
