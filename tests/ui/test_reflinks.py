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
