"""A CONVERSATION's own secrets, written from its own page (D216 rank 1).

The engine half was never missing: the scoped store is keyed by the RUN's slug, and a
conversation's slug is its directory name exactly as a routine's is, so
`secrets.d/<conversation-slug>.env` was already injected into its util calls and already
skipped the exposure gate. What was missing was any surface to PUT one through — so the only
credentials a conversation could use were the central store's, exposure decision and all.

These tests load what the operator sees, because the gap was a PAGE: the router shipped first
(`web/api_conversation_secrets.py`, pinned in tests/test_conversation_secrets.py) and a write
surface with no card is not the gap closed. The same shared card the routine page mounts, so
the assertions are deliberately the routine ones over a conversation slug.
"""

from playwright.sync_api import expect

from .helpers import start_conversation, visible_toast


def _open_secrets(ui_page):
    """The conversation's capabilities panel, open, with the own-secrets card rendered."""
    # the panel's OWN disclosure (`> summary`): the abilities cards inside it fold their met
    # requirements behind summaries of their own, so a descendant match is not unique.
    ui_page.locator(".conv-caps > summary").click()
    ui_page.wait_for_selector("[data-own-secret-key]")


def test_a_conversation_sets_its_own_secret(ui, ui_page):
    """Set a name through the conversation's card: it lands in THIS conversation's scoped
    store, and one that also exists centrally is labelled as shadowing it — the value never
    returns to the browser, because the API answers with names."""
    from rsched import secrets

    secrets.set_secret("IMMICH_EMAIL", "the-shared-one")
    slug, _conv = start_conversation(ui, ui_page, "sort the photo library")
    _open_secrets(ui_page)

    ui_page.locator("[data-own-secret-key]").fill("IMMICH_EMAIL")
    ui_page.locator("[data-own-secret-value]").fill("mine-only")
    ui_page.locator("[data-own-secret-set]").click()
    expect(visible_toast(ui_page)).to_contain_text("IMMICH_EMAIL saved")

    row = ui_page.locator('[data-own-secret="IMMICH_EMAIL"]')
    expect(row).to_be_visible()
    expect(row).to_contain_text("overrides the central store")
    assert secrets.load_routine_secrets(slug) == {"IMMICH_EMAIL": "mine-only"}
    assert secrets.load_secrets()["IMMICH_EMAIL"] == "the-shared-one"    # central untouched


def test_a_conversation_removes_its_own_secret(ui, ui_page):
    """And takes it away again — the DELETE reaches the conversation router, not the routine
    one, which is the whole parity: before this card the same press 404'd."""
    from rsched import secrets

    slug, _conv = start_conversation(ui, ui_page, "draft the quarterly note")
    _open_secrets(ui_page)

    ui_page.locator("[data-own-secret-key]").fill("DRAFT_TOKEN")
    ui_page.locator("[data-own-secret-value]").fill("v")
    ui_page.locator("[data-own-secret-set]").click()
    row = ui_page.locator('[data-own-secret="DRAFT_TOKEN"]')
    expect(row).to_be_visible()
    assert secrets.load_routine_secrets(slug) == {"DRAFT_TOKEN": "v"}

    row.get_by_role("button", name="remove").click()
    expect(visible_toast(ui_page)).to_contain_text("DRAFT_TOKEN removed")
    expect(ui_page.locator('[data-own-secret="DRAFT_TOKEN"]')).to_have_count(0)
    assert secrets.load_routine_secrets(slug) == {}


def test_the_card_asks_the_conversation_router(ui, ui_page):
    """The card is shared, so the thing that can silently regress is WHICH router it asks: a
    hard-coded `/api/routines/${slug}/secrets` (what it held before) renders identically and
    writes nowhere a conversation can read. Watch the requests the page actually makes."""
    slug, _conv = start_conversation(ui, ui_page, "check the mail rules")
    seen: list[str] = []
    ui_page.route("**/api/*/**/secrets**", lambda route: (seen.append(route.request.url),
                                                          route.continue_())[-1])
    _open_secrets(ui_page)
    ui_page.locator("[data-own-secret-key]").fill("ROUTED")
    ui_page.locator("[data-own-secret-value]").fill("v")
    ui_page.locator("[data-own-secret-set]").click()
    expect(ui_page.locator('[data-own-secret="ROUTED"]')).to_be_visible()

    assert seen, "the card made no secrets request at all"
    assert all(f"/api/conversations/{slug}/secrets" in url for url in seen), seen
    assert not any("/api/routines/" in url for url in seen), seen
