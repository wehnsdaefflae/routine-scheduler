"""The console's DOM helpers, evaluated as pure functions in the browser's ESM context.

util.js `el()` promises it has no HTML pathway, so a string handed to it can never become
markup. One attribute name broke that: an `on*` key whose value was not a function fell through
to setAttribute, and a string there IS an inline handler the browser compiles as script — under
any case, since HTML lowercases attribute names. It is refused now.

landing.js `openFolds` and `flash` are the two moves of every landing journey (a ref link, a
settings field, a setup fix, the side TOC), each of which used to write them out by hand.
"""

from __future__ import annotations

_EL_PROBE = """() => import('/static/util.js').then((m) => {
    const refused = (attrs) => {
        try { m.el('button', attrs); return null; } catch (e) { return e.name; }
    };
    let clicks = 0;
    const wired = m.el('button', { onclick: () => { clicks += 1; } }, 'x');
    wired.click();
    const camel = m.el('button', { onClick: () => { clicks += 10; } }, 'y');
    camel.click();
    const absent = m.el('button', { onclick: null, class: null, title: false }, 'z');
    return {
        lower: refused({ onclick: 'window.pwned = 1' }),
        upper: refused({ ONCLICK: 'window.pwned = 1' }),
        mixed: refused({ onMouseOver: 'window.pwned = 1' }),
        truthy: refused({ onload: true }),
        clicks,
        absentAttrs: absent.getAttributeNames(),
        pwned: window.pwned === 1,
    };
})"""


def test_el_refuses_an_inline_handler_string(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate(_EL_PROBE)
    assert (out["lower"], out["upper"], out["mixed"], out["truthy"]) == ("TypeError",) * 4
    assert out["pwned"] is False
    assert out["clicks"] == 11            # a function is wired, under any case of its name
    assert out["absentAttrs"] == []       # null/false still mean "not set" — class included


def test_a_landing_opens_every_fold_above_and_flashes_once(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate("""() => Promise.all([import('/static/util.js'),
                                                  import('/static/landing.js')])
      .then(async ([m, landing]) => {
        const inner = m.el('details', {}, m.el('summary', {}, 'in'), m.el('p', {}, 'target'));
        const outer = m.el('details', {}, m.el('summary', {}, 'out'), inner);
        const aside = m.el('details', {}, m.el('summary', {}, 'aside'));
        document.body.append(m.el('div', {}, outer, aside));
        const target = inner.querySelector('p');
        landing.openFolds(target);
        landing.flash(target);
        const lit = target.classList.contains('ref-flash');
        await new Promise((r) => setTimeout(r, 2700));
        return { outer: outer.open, inner: inner.open, aside: aside.open, lit,
                 after: target.classList.contains('ref-flash') };
    })""")
    assert (out["outer"], out["inner"], out["aside"]) == (True, True, False)
    assert out["lit"] is True and out["after"] is False   # dropped, so a re-landing animates
