"""A CONVERSATION's HELD grants, read and withdrawn from its own page (D216 rank 1).

F783 gave the operator the DENY half of a conversation's `grants:` mapping (the tombstones, and
the press that clears one — tests/ui/test_conversation_declined_card.py). The ALLOW half had no
surface at all: a `grants:` TRUE row — what a Decisions `allow_forever` writes for the one class
with no native switch, `secret:` (`entities.TRUE_ROW_CLASSES`,
`web/grants_apply.apply_forever`) — was honoured by the engine for a conversation exactly as for
a routine and shown nowhere, so the operator could not read what a thread had been granted, nor
take it back short of hand-editing routine.yaml.

The gap was a PAGE, so these tests load what the operator sees. The router is pinned in
tests/test_conversation_grants.py; a write surface with no card is not the gap closed, which is
why every assertion here is on the VISIBLE row and on the press that withdraws it. Each
assertion about a row's content follows a presence assertion on that row: a selector that
matches nothing reads as a pass over a console that lost the card entirely.
"""

import yaml
from playwright.sync_api import expect

from .helpers import start_conversation, visible_toast


def _grant(conv, *entities) -> None:
    """Write the allow-forever rows a Decisions-page allow_forever leaves for a `secret:`
    entity (grants_apply.apply_forever → config.routine.record_grants), as a hand edit of the
    conversation's own config."""
    path = conv / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["grants"] = {**(cfg.get("grants") or {}), **dict.fromkeys(entities, True)}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _decline(conv, *entities) -> None:
    """A deny tombstone in the SAME mapping — what must survive a withdrawal from the other
    half, and what must never appear in this card."""
    path = conv / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["grants"] = {**(cfg.get("grants") or {}), **dict.fromkeys(entities, False)}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _grants(conv) -> dict:
    return (yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8"))
            or {}).get("grants") or {}


def _open_caps(ui_page):
    """The conversation's capabilities panel, open, with the held-grants card rendered."""
    # the panel's OWN disclosure (`> summary`): the abilities cards inside it fold their met
    # requirements behind summaries of their own, so a descendant match is not unique.
    ui_page.locator(".conv-caps > summary").click()
    ui_page.wait_for_selector("[data-held-card]")


def test_a_conversation_shows_what_it_holds_and_withdraws_one(ui, ui_page):
    """The whole finding, through the browser: the row an allow-forever left is VISIBLE on the
    conversation's own page, and the press returns the entity to undecided."""
    _slug, conv = start_conversation(ui, ui_page, "watch the ntfy topic")
    _grant(conv, "secret:NTFY_URL", "secret:ZULIP_API_KEY")
    _decline(conv, "util:signal")
    ui_page.reload()
    _open_caps(ui_page)

    row = ui_page.locator('[data-held-row="secret:NTFY_URL"]')
    expect(row).to_have_count(1)
    expect(row).to_contain_text("secret:NTFY_URL")
    expect(ui_page.locator('[data-held-row="secret:ZULIP_API_KEY"]')).to_have_count(1)
    # the deny half of the same mapping is NOT in this card — it has its own, with its own press
    expect(ui_page.locator('[data-held-row="util:signal"]')).to_have_count(0)
    expect(ui_page.locator('[data-declined-row="util:signal"]')).to_have_count(1)

    row.get_by_role("button", name="remove").click()
    expect(visible_toast(ui_page)).to_contain_text("secret:NTFY_URL")
    expect(ui_page.locator('[data-held-row="secret:NTFY_URL"]')).to_have_count(0)
    # one entity per press, never a wholesale rewrite: the other grant and the tombstone stay
    expect(ui_page.locator('[data-held-row="secret:ZULIP_API_KEY"]')).to_have_count(1)
    assert _grants(conv) == {"secret:ZULIP_API_KEY": True, "util:signal": False}, _grants(conv)


def test_a_conversation_holding_nothing_says_so(ui, ui_page):
    """The card is always mounted: "nothing granted forever" is the state the operator needs to
    read too, otherwise an absent card and an empty one look the same."""
    _slug, _conv = start_conversation(ui, ui_page, "draft the release note")
    _open_caps(ui_page)
    card = ui_page.locator("[data-held-card]")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("nothing granted forever")


def test_the_card_asks_the_conversation_grants_router(ui, ui_page):
    """The card is parameterized by `base` and `path`, so what can silently regress is WHICH
    router it asks: pointed at the declined half it would render identically off the wrong rows.
    Watch the requests the page actually makes."""
    slug, conv = start_conversation(ui, ui_page, "check the mail rules")
    _grant(conv, "secret:GMAIL_ADDRESS")
    seen: list[str] = []
    ui_page.route("**/api/*/**/grants**", lambda route: (seen.append(route.request.url),
                                                         route.continue_())[-1])
    ui_page.reload()
    _open_caps(ui_page)
    row = ui_page.locator('[data-held-row="secret:GMAIL_ADDRESS"]')
    expect(row).to_have_count(1)
    row.get_by_role("button", name="remove").click()
    expect(ui_page.locator('[data-held-row="secret:GMAIL_ADDRESS"]')).to_have_count(0)

    assert seen, "the card made no grants request at all"
    assert all(f"/api/conversations/{slug}/grants" in url for url in seen), seen
    assert not any("/api/routines/" in url for url in seen), seen
