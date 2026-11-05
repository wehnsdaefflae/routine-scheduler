"""A CONVERSATION's SECRET EXPOSURE dial, on its own page (D216 part a).

A routine's settings page has carried a three-state control per central-store secret since D103
(`static/views/routine-config-access.js`: "ask on first use" / "expose" / "withhold"). A
conversation had none: the two clear routes beside this one (F783's tombstones, D216 rank 1's
held grants) can only remove a row that already EXISTS, so the exposure decision for a thread
was reachable only by the engine asking at first use, and withholding a name in advance meant
hand-editing routine.yaml.

The gap was a PAGE, so these tests load what the operator sees and drive the dial: the router is
pinned in tests/test_conversation_secret_exposure.py, and a write surface with no control is not
the gap closed. Each assertion about a row's state follows a presence assertion on that row — a
selector that matches nothing reads as a pass over a console that lost the card entirely.
"""

import yaml
from playwright.sync_api import expect

from rsched import secrets

from .helpers import start_conversation, visible_toast


def _store(*names) -> None:
    """Names in the CENTRAL store — what the dial offers a row for. The suite's hermetic home
    redirects `secrets_path()` (tests/conftest.py sets RSCHED_CONFIG), so this writes a fixture
    store and never the host's."""
    for name in names:
        secrets.set_secret(name, "fixture-value")


def _grants(conv) -> dict:
    return (yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8"))
            or {}).get("grants") or {}


def _set_grants(conv, rows: dict) -> None:
    """Rows already in the mapping — what a change must not disturb."""
    path = conv / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["grants"] = {**(cfg.get("grants") or {}), **rows}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _open_caps(ui_page):
    """The conversation's capabilities panel, open, with the exposure card rendered."""
    # the panel's OWN disclosure (`> summary`): the cards inside it fold their own details
    # behind summaries, so a descendant match is not unique.
    ui_page.locator(".conv-caps > summary").click()
    ui_page.wait_for_selector("[data-exposure-card]")


def test_a_conversation_exposes_a_store_secret_from_its_own_page(ui, ui_page):
    """The whole gap, through the browser: a secret nothing had asked for is handed over in
    advance, and the row lands in the conversation's own routine.yaml — the row the engine
    reads (`engine/secretgate.secret_state`)."""
    _store("NTFY_URL", "ZULIP_API_KEY")
    _slug, conv = start_conversation(ui, ui_page, "watch the ntfy topic")
    _set_grants(conv, {"util:signal": False})
    ui_page.reload()
    _open_caps(ui_page)

    row = ui_page.locator('[data-secret-row="NTFY_URL"]')
    expect(row).to_have_count(1)
    expect(row).to_contain_text("NTFY_URL")
    dial = row.locator("select")
    expect(dial).to_have_value("")                     # undecided — asked on first use
    dial.select_option("true")
    expect(visible_toast(ui_page)).to_contain_text("NTFY_URL")
    expect(ui_page.locator('[data-secret-row="NTFY_URL"] select')).to_have_value("true")
    # ONE named row per change: the deny tombstone in the same mapping survives, which is what
    # a wholesale replace from a panel that cannot re-read first would have dropped.
    assert _grants(conv) == {"util:signal": False, "secret:NTFY_URL": True}, _grants(conv)
    # the name nobody decided stays undecided
    expect(ui_page.locator('[data-secret-row="ZULIP_API_KEY"] select')).to_have_value("")


def test_all_three_states_are_reachable_from_the_dial(ui, ui_page):
    """withhold → expose → back to "ask on first use", the routine dial's three states. The
    third is the ABSENCE of a row, so the last change must DELETE it."""
    _store("GMAIL_ADDRESS")
    _slug, conv = start_conversation(ui, ui_page, "check the mail rules")
    _open_caps(ui_page)

    # The dial's own value settles the moment the browser changes the select — BEFORE the PUT
    # returns — so the row's state is not a wait for the write. Each step waits on the TOAST,
    # which `act` raises from the response, and only then reads the file.
    dial = ui_page.locator('[data-secret-row="GMAIL_ADDRESS"] select')
    expect(dial).to_have_count(1)
    dial.select_option("false")
    expect(visible_toast(ui_page)).to_contain_text("GMAIL_ADDRESS: withheld")
    assert _grants(conv) == {"secret:GMAIL_ADDRESS": False}, _grants(conv)

    ui_page.locator('[data-secret-row="GMAIL_ADDRESS"] select').select_option("true")
    expect(visible_toast(ui_page)).to_contain_text("GMAIL_ADDRESS: exposed")
    assert _grants(conv) == {"secret:GMAIL_ADDRESS": True}, _grants(conv)

    ui_page.locator('[data-secret-row="GMAIL_ADDRESS"] select').select_option("")
    expect(visible_toast(ui_page)).to_contain_text("GMAIL_ADDRESS: asked on first use")
    assert _grants(conv) == {}, _grants(conv)
    # and the reload the card does from the response leaves the dial on the state the server
    # reported, not the one the browser set
    expect(ui_page.locator('[data-secret-row="GMAIL_ADDRESS"] select')).to_have_value("")


def test_the_dial_shows_what_a_decisions_click_already_granted(ui, ui_page):
    """A row written elsewhere (a Decisions `allow_forever`) is the dial's own state when the
    page loads — otherwise the control would offer "ask on first use" for a secret the thread
    already holds, and the operator's next change would be made on a false reading."""
    _store("NTFY_URL")
    _slug, conv = start_conversation(ui, ui_page, "post the digest")
    _set_grants(conv, {"secret:NTFY_URL": True})
    ui_page.reload()
    _open_caps(ui_page)
    row = ui_page.locator('[data-secret-row="NTFY_URL"]')
    expect(row).to_have_count(1)
    expect(row.locator("select")).to_have_value("true")
    # and the held-grants card beside it still reads the same row (the two surfaces agree)
    expect(ui_page.locator('[data-held-row="secret:NTFY_URL"]')).to_have_count(1)


def test_a_row_the_store_no_longer_carries_is_shown_and_flagged(ui, ui_page):
    """A decision the engine still honours must stay visible and changeable: a `secret:` row
    whose name has left the store is rendered as a stale entry rather than hidden, exactly as
    on the routine page."""
    _slug, conv = start_conversation(ui, ui_page, "tidy the backlog")
    _set_grants(conv, {"secret:GONE_KEY": True})
    ui_page.reload()
    _open_caps(ui_page)
    row = ui_page.locator('[data-secret-row="GONE_KEY"]')
    expect(row).to_have_count(1)
    expect(row).to_contain_text("stale entry")
    row.locator("select").select_option("")
    expect(ui_page.locator('[data-secret-row="GONE_KEY"]')).to_have_count(0)
    assert _grants(conv) == {}, _grants(conv)


def test_the_card_asks_the_conversation_router(ui, ui_page):
    """The card is parameterized by `base`, so what can silently regress is WHICH router it
    writes to — pointed at a routine it would appear to work while editing another subject's
    config. Watch the requests the page actually makes."""
    _store("NTFY_URL")
    slug, _conv = start_conversation(ui, ui_page, "watch the topic")
    seen: list[str] = []
    # `**/api/**/secret-exposure**`, not `**/secret-exposure**`: the latter also matches the
    # card's own ES module (`/static/components/secret-exposure.js`), so the assertion below
    # would fail over a page that asked the right router.
    ui_page.route("**/api/**/secret-exposure**", lambda route: (seen.append(route.request.url),
                                                                route.continue_())[-1])
    ui_page.reload()
    _open_caps(ui_page)
    dial = ui_page.locator('[data-secret-row="NTFY_URL"] select')
    expect(dial).to_have_count(1)
    dial.select_option("true")
    expect(visible_toast(ui_page)).to_contain_text("NTFY_URL: exposed")

    assert seen, "the card made no secret-exposure request at all"
    assert all(f"/api/conversations/{slug}/secret-exposure" in url for url in seen), seen
    assert not any("/api/routines/" in url for url in seen), seen
