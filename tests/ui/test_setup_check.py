"""The setup check on the routine page — the surface, rendered where somebody will see it."""
from __future__ import annotations

import yaml
from playwright.sync_api import expect


def _hold_util(ui, slug: str, name: str, *, secrets: str = "(none)", fs: str = "none") -> None:
    """Give the fixture routine a reserved util whose header declares something it lacks."""
    d = ui.server_cfg.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        f'"""{name} — t.\n\nusage: gu {name}\ncalls: (none)\ntags: t\n'
        f'secrets: {secrets}\nnet: none\nfs: {fs}\n"""\n', encoding="utf-8")
    path = ui.routines / slug / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Hold NOTHING else: writing an explicit capabilities mapping replaces the model's default
    # (write_util + the memory pair), which would orphan the default permissions and add rows
    # this test is not about. One util, one gap, one row to assert.
    cfg["permissions"] = []
    cfg["capabilities"] = {"actions": [], "utils": [name], "util_tags": []}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_a_ready_routine_shows_no_strip(ui_page, ui):
    """A panel that is always there is a panel nobody reads."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("[data-routine-hero], h1")
    ui_page.wait_for_timeout(400)
    assert ui_page.locator("[data-setup-check]").count() == 0


def test_a_blocking_gap_is_named_above_the_panels(ui_page, ui):
    """The case the whole surface exists for: a util whose private store no grant covers, which
    was previously discoverable only by the run failing."""
    _hold_util(ui, "uir", "sig", fs="rw /srv/sig-sessions")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    strip = ui_page.locator("[data-setup-check]")
    strip.wait_for(state="visible", timeout=10000)
    assert "has-blocks" in (strip.get_attribute("class") or "")
    row = ui_page.locator('.setup-row[data-entity="fs-write:/srv/sig-sessions"]')
    assert row.count() == 1
    assert "fails" in row.locator(".setup-sev").inner_text().lower()
    assert "cannot reach it" in row.inner_text()
    # and the strip leads with the count, so the page says how bad it is before saying why
    assert "will fail" in ui_page.locator(".setup-head").inner_text()


def test_a_note_renders_without_making_the_strip_look_broken(ui_page, ui):
    """The three severities have to READ apart, or the strip is a wall of equal-looking rows.
    A routine that may rewrite its own instructions is the note case: intended for an
    improver, worth saying out loud everywhere else, and nothing is failing.

    (What each severity MEANS is pinned in tests/test_surface.py — the store cannot be seeded
    from here, so this asserts what the page can actually show: the row, its class, its wording.)
    """
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # hold nothing else: an explicit capabilities mapping replaces the model default, which
    # would orphan the default permissions and add rows this test is not about
    cfg["permissions"] = []
    cfg["capabilities"] = {"actions": ["write_recipe"], "utils": [], "util_tags": []}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/routine/uir")
    strip = ui_page.locator("[data-setup-check]")
    strip.wait_for(state="visible", timeout=10000)
    assert "has-blocks" not in (strip.get_attribute("class") or "")   # a note is not a failure
    row = ui_page.locator('.setup-row[data-entity="action:write_recipe"]')
    assert row.count() == 1
    assert "sev-note" in (row.get_attribute("class") or "")
    assert "rewrite its own instructions" in row.inner_text()
    # the orphan check reaches the same entity (nothing here requires the capability), and one
    # entity gets ONE row — two rows saying different things about it would read as a bug
    assert ui_page.locator(".setup-row.sev-note").count() == 1


# --- ability cards: the join the two-column panel asked the reader to do -------------------

def test_a_resolved_need_appears_inside_the_ability_that_owns_it(ui_page, ui):
    """The point of the card view. Under the two-column panel, seeing whether "reach a person
    on Discord" actually worked meant reading the doc column, the capability column, and then
    two other panels. Here the session store the util declares sits in the card for the doc
    that reserves it, attributed by the surface's machine-readable `source` rather than by
    parsing its prose."""
    d = ui.server_cfg.libraries_home / "utils" / "discord"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        '"""discord — t.\n\nusage: gu discord\ncalls: (none)\ntags: t\n'
        'secrets: (none)\nnet: none\nfs: rw /srv/discord-state\n"""\n', encoding="utf-8")
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["permissions"] = ["messaging-discord"]
    cfg["capabilities"] = {"actions": [], "utils": ["discord"], "util_tags": []}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/routine/uir")
    card = ui_page.locator('.ability[data-ability="messaging-discord"]')
    card.wait_for(state="visible", timeout=10000)
    # the capability it requires AND the store that capability turned out to need, together
    assert card.locator('.ab-row[data-entity="discord"]').count() >= 1
    store = card.locator('.ab-row[data-entity="/srv/discord-state"]')
    assert store.count() == 1
    assert "blocks" in (store.get_attribute("class") or "")
    # and the card's own badge states the verdict without the reader adding it up
    assert "will fail" in card.locator(".pill").inner_text()


def test_an_ability_that_is_off_is_a_catalogue_row_not_an_alarm(ui_page, ui):
    """An unheld ability has nothing outstanding, so it gets no requirement stack and no state
    dots. Rendering its requirements as unmet painted the page red for things that were merely
    not switched on — which said the opposite of the truth."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    row = ui_page.locator('.avail-row[data-ability="shell"]')
    row.wait_for(state="visible", timeout=10000)
    assert row.locator("input[type=checkbox]").is_checked() is False
    assert row.locator(".dot").count() == 0
    assert ui_page.locator('.ability[data-ability="shell"]').count() == 0


def test_a_toggle_states_both_sides_and_when_to_hold_it(ui, ui_page):
    """Operator, 2026-08-30: the descriptions "don't provide actionable information" and "the
    control element is a toggle?! how are you supposed to know what 'on' means?!".

    Neither the doc's TITLE nor its BODY can answer that. A title names a topic ("ask policy —
    when and how to involve the user"); the body is written to the RUN in the imperative ("read
    the error before you try again"), which instructs the agent rather than describing anything
    to the person choosing. A toggle is a COMPARISON, so the row carries both sides plus the
    decision it actually asks: is this one for THIS routine?
    """
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Permissions & capabilities')", timeout=10_000)

    held = ui_page.locator('.ability[data-ability="memory"] [data-effect="memory"]')
    expect(held).to_be_visible(timeout=10_000)
    # both sides are present, and the one the routine is actually in is the emphasised one
    expect(held.locator('[data-effect-side="with"]')).to_have_class("effect-side active")
    expect(held.locator('[data-effect-side="without"]')).not_to_have_class("effect-side active")
    expect(held).to_contain_text("notebook")
    expect(held).to_contain_text("starts every run knowing only its recipe")
    # …and the third field, which answers whether this one is for THIS routine
    expect(held.locator(".effect-side.advice")).to_contain_text("keeps hitting the same surprises")

    # …and an ability it does NOT hold emphasises the other side, from the same three fields
    avail = ui_page.locator('.avail-row[data-ability="shell"] [data-effect="shell"]')
    expect(avail.locator('[data-effect-side="without"]')).to_have_class("effect-side active")
    expect(avail).to_contain_text("run arbitrary shell commands")
    expect(avail).to_contain_text("escape hatch")
    # nothing anywhere falls back to the placeholder, which is what a missing effect: renders
    expect(ui_page.locator(".effect-text.missing")).to_have_count(0)


def test_a_rule_toggle_states_both_sides_too(ui, ui_page):
    """Same three fields on the rules panel — a rule's on/off difference is the one thing the
    principle prose never states, because it is written as if it always applies."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('General rules')", timeout=10_000)
    bound = ui_page.locator('.rule-bound[data-rule="ask-policy"] [data-effect="ask-policy"]')
    expect(bound).to_be_visible(timeout=10_000)
    expect(bound).to_contain_text("interrupts you only for a decision that is genuinely yours")
    expect(bound).to_contain_text("asks you whenever it is unsure")
    expect(bound).to_contain_text("runs unattended")
    expect(bound.locator('[data-effect-side="with"]')).to_have_class("effect-side active")


def test_no_effect_row_overflows_the_box_it_is_in(ui, ui_page):
    """The layout half of the same order — "ugly AND broken" (operator, 2026-08-30).

    Three separate faults shipped in one row, and all three are the same shape: text that
    silently overflows instead of wrapping, which no assertion about CONTENT can see.

    1. A grid item defaults to `min-width: auto`, so a bare `1fr` text column refuses to
       shrink below its longest line — the sentence was clipped at the card edge.
    2. The `when` row carried a `.when` class, which is the console's TIMESTAMP class in
       base.css and brings `white-space: nowrap`; that one row alone never wrapped.
    3. `.rule-line`'s template did not line up with its children — `190px` was landing on the
       checkbox — so the description got an `auto` (max-content) column and the name was drawn
       on top of it.

    scrollWidth > clientWidth is what all three look like from the outside, so that is the
    assertion. It is made on the REAL page at a real width, because none of it reproduces in
    a unit test of the component.
    """
    ui_page.set_viewport_size({"width": 1400, "height": 1000})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('General rules')", timeout=10_000)
    ui_page.wait_for_selector(".effect-side", timeout=10_000)
    over = ui_page.evaluate("""() => {
      const bad = [];
      for (const n of document.querySelectorAll('.effect-side, .effect-text')) {
        if (n.scrollWidth > n.clientWidth + 1) {
          bad.push(n.className + ' ' + n.clientWidth + '<' + n.scrollWidth
                   + ' :: ' + n.textContent.slice(0, 40));
        }
      }
      return bad;
    }""")
    assert over == [], f"effect rows overflow their box (clipped, not wrapped): {over}"

    # …and the row's own columns line up with its children: the name and the description must
    # not share a cell, which is what drew one on top of the other.
    boxes = ui_page.evaluate("""() => {
      const r = document.querySelector('.rule-bound .rule-line');
      const name = r.querySelector('.rule-name').getBoundingClientRect();
      const eff = r.querySelector('.effect-line').getBoundingClientRect();
      return {nameRight: name.right, effLeft: eff.left};
    }""")
    assert boxes["effLeft"] >= boxes["nameRight"], (
        f"the rule name and its description overlap: {boxes}")
