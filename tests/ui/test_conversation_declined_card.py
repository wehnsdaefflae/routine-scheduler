"""A CONVERSATION's declined access, cleared from its own page (F783).

The gap was a PAGE, so these tests load what the operator sees. A `deny_forever` on the
Decisions page writes a `{entity: false}` tombstone into the conversation's own routine.yaml and
the engine then refuses that entity and stops asking — and the only editor that removed such a
row was the ROUTINE page's "Declined access" block, which no conversation has. One mis-click
denied a capability for the life of the thread.

The router shipped first (`web/api_conversation_grants.py`, pinned in
tests/test_conversation_grants.py); a write surface with no card is not the gap closed, which is
why the assertions here are on the VISIBLE row and on the press that clears it.
"""

import yaml
from playwright.sync_api import expect

from .helpers import start_conversation, visible_toast


def _decline(conv, *entities) -> None:
    """Write the tombstones a Decisions-page deny_forever leaves (grants_apply.apply_forever →
    config.routine.record_grants), as a hand edit of the conversation's own config."""
    path = conv / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["grants"] = {**(cfg.get("grants") or {}), **dict.fromkeys(entities, False)}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _grants(conv) -> dict:
    return (yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8"))
            or {}).get("grants") or {}


def _open_caps(ui_page):
    """The conversation's capabilities panel, open, with the declined-access card rendered."""
    # the panel's OWN disclosure (`> summary`): the abilities cards inside it fold their met
    # requirements behind summaries of their own, so a descendant match is not unique.
    ui_page.locator(".conv-caps > summary").click()
    ui_page.wait_for_selector("[data-declined-card]")


def test_a_conversation_shows_its_declined_rows_and_clears_one(ui, ui_page):
    """The whole finding, through the browser: the row the operator's deny left is VISIBLE on
    the conversation's own page, and the press puts the entity back to undecided."""
    _slug, conv = start_conversation(ui, ui_page, "watch the signal threads")
    _decline(conv, "util:signal", "action:shell")
    ui_page.reload()
    _open_caps(ui_page)

    row = ui_page.locator('[data-declined-row="util:signal"]')
    expect(row).to_be_visible()
    expect(row).to_contain_text("util:signal")
    expect(ui_page.locator('[data-declined-row="action:shell"]')).to_be_visible()

    row.get_by_role("button", name="remove").click()
    expect(visible_toast(ui_page)).to_contain_text("util:signal")
    expect(ui_page.locator('[data-declined-row="util:signal"]')).to_have_count(0)
    # the other tombstone is untouched — one entity per press, never a wholesale rewrite
    expect(ui_page.locator('[data-declined-row="action:shell"]')).to_be_visible()
    assert _grants(conv) == {"action:shell": False}, _grants(conv)


def test_a_conversation_with_nothing_declined_says_so(ui, ui_page):
    """The card is always mounted: "nothing declined" is the state the operator needs to read
    too, otherwise an absent card and an empty one look the same."""
    _slug, _conv = start_conversation(ui, ui_page, "draft the release note")
    _open_caps(ui_page)
    expect(ui_page.locator("[data-declined-card]")).to_contain_text("nothing declined")


def test_the_card_asks_the_conversation_router(ui, ui_page):
    """The card is parameterized by `base`, so what can silently regress is WHICH router it
    asks: pointed at `/api/routines/${slug}` it renders identically and 404s, because that slug
    is not a routine. Watch the requests the page actually makes."""
    slug, conv = start_conversation(ui, ui_page, "check the mail rules")
    _decline(conv, "util:mail")
    seen: list[str] = []
    ui_page.route("**/api/*/**/declined**", lambda route: (seen.append(route.request.url),
                                                           route.continue_())[-1])
    ui_page.reload()
    _open_caps(ui_page)
    ui_page.locator('[data-declined-row="util:mail"]').get_by_role(
        "button", name="remove").click()
    expect(ui_page.locator('[data-declined-row="util:mail"]')).to_have_count(0)

    assert seen, "the card made no declined request at all"
    assert all(f"/api/conversations/{slug}/declined" in url for url in seen), seen
    assert not any("/api/routines/" in url for url in seen), seen
