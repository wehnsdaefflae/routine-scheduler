"""The console at 390px — the layouts that break only on a phone.

test_mobile_nav.py owns the ONE invariant that holds the bottom bar together (the document
never scrolls sideways) and asserts it per TOP-LEVEL route. This file owns what that route list
cannot reach:

* the widgets that widen a document only once they carry real content — a `<select>` whose
  options are model names, a provider error printed verbatim as an account status;
* the DETAIL routes (a conversation, a run, a library document), which are where those widgets
  live and which no route-level test visited;
* and the phone layouts themselves: a five-column table stacked into cards, a bottom bar whose
  glyphs must be distinguishable without their labels, a breadcrumb that must not wrap, an idle
  dock that must not sit on the page.

Everything here clips against the width the test emulated, never against `window.innerWidth` —
an overflowing document inflates that value, so a guard written with it agrees with the bug.
"""

from __future__ import annotations

from playwright.sync_api import expect

from rsched import lanes
from rsched.config import EndpointConfig
from rsched.endpoints import cliproxy_login, cliproxy_quota

from .test_mobile_nav import LONG_TOKEN, PHONE

DESKTOP = {"width": 1400, "height": 900}

#: A provider payload as it actually arrives: `{"error":{"type":"usage_limit_reached",…}}` with
#: not one break opportunity in it. On 2026-09-22 this string alone made the whole Settings
#: document 624px wide on a 375px phone — and the bottom bar is laid out against that width.
RAW_PROVIDER_ERROR = (
    '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached",'
    '"plan_type":"prolite","resets_at":1790467371,"eligible_promo":null,"resets_in":420000}}')


def _doc_width(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth")


def _start_conversation(ui, ui_page) -> str:
    """One conversation, made the way a person makes one (the composer posts and routes)."""
    ui_page.set_viewport_size(DESKTOP)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Look at the console on a phone.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    return ui_page.url.rsplit("/", 1)[-1]


def test_a_dropdown_cannot_widen_a_conversation_on_a_phone(ui, ui_page):
    """A `<select>` is as wide as its widest OPTION, and inside a shrink-to-fit row (the
    conversation head's model pair was `display: inline-flex`) `max-width: 100%` has nothing
    definite to resolve against — so a long model name widened the DOCUMENT to 742px and took
    the fixed bottom bar off-screen with it. The fixture catalog holds one model called "m", so
    the option is injected here: what is being pinned is the layout, not the catalog.
    """
    slug = _start_conversation(ui, ui_page)
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/conversations/{slug}")
    sel = ui_page.locator(".conv-model select").first
    expect(sel).to_be_visible()
    ui_page.evaluate(
        """(name) => document.querySelectorAll(".conv-model select").forEach((s) => {
            const o = document.createElement("option");
            o.value = name; o.textContent = name; o.selected = true; s.append(o);
        })""", f"Fable Max Reasoning {LONG_TOKEN}")
    ui_page.wait_for_timeout(120)
    assert _doc_width(ui_page) <= PHONE["width"] + 1, (
        f"a model name widened the conversation to {_doc_width(ui_page)}px")


def test_a_raw_provider_error_cannot_widen_settings_on_a_phone(ui, ui_page, monkeypatch):
    """The account line prints what the provider said, and what the provider says can be an
    unbreakable JSON body. Settings must break it, not pan sideways: the operator reads this
    page on a phone precisely when an account has stopped working.
    """
    ui.server_cfg.endpoints["claude-proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1", quota_source="cliproxy")
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: {"supported": False})
    monkeypatch.setattr(cliproxy_login, "accounts", lambda cfg, providers: {
        "supported": True, "ok": True,
        "accounts": [{"name": "claude-me.json", "provider": "claude", "label": "me@example.org",
                      "status": "error", "status_message": RAW_PROVIDER_ERROR,
                      "unavailable": True, "disabled": False, "next_retry_after": ""}],
        "providers": [{"id": "anthropic", "label": "Claude"}]})

    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    line = ui_page.locator(".proxy-accounts").first
    expect(line).to_contain_text("usage_limit_reached")
    assert _doc_width(ui_page) <= PHONE["width"] + 1, (
        f"a provider error widened Settings to {_doc_width(ui_page)}px")


def test_a_warning_line_wears_the_warning_colour(ui, ui_page, monkeypatch):
    """`.warn-line` was named in six places and defined in no stylesheet, so every failure it
    marked — a provider error, a quota refusal — rendered as ordinary body ink."""
    ui.server_cfg.endpoints["claude-proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1", quota_source="cliproxy")
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: {"supported": False})
    monkeypatch.setattr(cliproxy_login, "accounts", lambda cfg, providers: {
        "supported": False, "ok": False, "error": "the proxy is not answering"})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    ui_page.wait_for_selector("#sec-endpoints")
    colours = ui_page.evaluate(
        """() => {
            const n = document.createElement("span");
            n.className = "warn-line"; n.textContent = "x";
            document.body.append(n);
            const c = getComputedStyle(n).color;
            const plain = getComputedStyle(document.body).color;
            n.remove();
            return [c, plain];
        }""")
    assert colours[0] != colours[1], "`.warn-line` is styled by no stylesheet"


def test_the_bottom_bar_gives_every_destination_its_own_glyph(ui, ui_page):
    """Below 1180px the rail drops its labels, so the glyph IS the destination. Decisions and
    Help both drew a circled question mark with a dot, which made the one place the palette
    calls SUMMONS indistinguishable from the documentation.
    """
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".topbar nav a[data-nav]").first).to_be_visible()
    shapes = ui_page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll(".topbar nav a[data-nav]")]
            .map((a) => [a.dataset.nav,
                         [...a.querySelectorAll("svg *")].map((n) => n.getAttribute("d")
                           || n.tagName + (n.getAttribute("r") || "")).join("|")]))""")
    assert shapes["questions"] != shapes["help"], (
        "Decisions and Help draw the same glyph, and the phone shows no labels")
    assert len(set(shapes.values())) == len(shapes), f"two destinations share a glyph: {shapes}"


def test_a_stacked_table_keeps_every_cell_on_a_phone_screen(ui, ui_page):
    """`.tablewrap` scrolls sideways with nothing saying so, which on a phone is a column
    nobody knows is there: the library lost the cell that says what a document IS, the secrets
    table crushed "needed by" to two characters, and a lane row's controls sat off-screen. A
    `table.list.stack` drops its columns below 860px and lays the row out as lines.
    """
    ui_page.set_viewport_size(PHONE)
    for route, ready in (("#/routines", "table.list.stack"), ("#/library", "table.list.stack")):
        ui_page.goto(f"{ui.url}/{route}")
        ui_page.wait_for_selector(ready)
        ui_page.wait_for_timeout(300)
        spilled = ui_page.evaluate(
            """(width) => [...document.querySelectorAll("table.list.stack td")]
                .filter((td) => td.textContent.trim()
                    && td.getBoundingClientRect().right > width + 1)
                .map((td) => td.textContent.trim().slice(0, 48))""", PHONE["width"])
        assert not spilled, f"{route}: cells past the screen edge: {spilled}"


def test_a_lane_row_offers_its_controls_on_a_phone(ui, ui_page):
    """The lane header's run / pause / edit live at the far right of a five-column table, so
    eight of eleven lanes offered no control at all at 390px — and "run a routine" from the
    dashboard was impossible without discovering a horizontal scroll with no affordance.
    """
    lanes.create(ui.routines, name="Every weekday morning before the standup",
                 members=[{"slug": "uir"}], cron="0 7 * * 1-5")
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/routines")
    run = ui_page.locator("[data-lane-run]").first
    expect(run).to_be_visible()
    box = run.bounding_box()
    assert box and box["x"] + box["width"] <= PHONE["width"] + 1, (
        f"the lane's run control sits off-screen at {box}")


def test_the_page_name_is_not_repeated_over_every_phone_page(ui, ui_page):
    """A one-segment crumb is the page's own name, which the h1 under it and the highlighted
    bottom-bar icon already say; a DEEP crumb locates you and keeps its row — on ONE row, not
    the five it wrapped into beside the search box.
    """
    ui.seed_run("uir", "20260904-000353", "finished", summary="done")
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator("#crumbs .here")).to_be_hidden()

    ui_page.goto(f"{ui.url}/#/run/uir:20260904-000353")
    ui_page.wait_for_selector(".run-main")
    here = ui_page.locator("#crumbs .here")
    expect(here).to_be_visible()                       # a deep crumb still locates you
    bar = ui_page.locator(".workbar").bounding_box()
    assert bar and bar["height"] <= 70, f"the workbar wraps to {bar['height']:.0f}px on a phone"


def test_the_idle_llm_dock_is_a_glyph_on_a_phone(ui, ui_page):
    """Idle, the dock is a 100px pill over the bottom-right corner of every page — it covered
    the lane default-on-failure select, a routine's Setup check head, a chart's remove button.
    Collapsed to its bolt it covers a margin; a dock with work in it wears its count again.
    """
    ui_page.set_viewport_size(DESKTOP)
    ui_page.goto(f"{ui.url}/#/routines")
    pill = ui_page.locator(".lt-pill")
    expect(pill).to_be_visible()
    wide = pill.bounding_box()["width"]

    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(pill).to_be_visible()
    narrow = pill.bounding_box()["width"]
    assert narrow < wide, f"the idle dock is no smaller on a phone ({narrow}px vs {wide}px)"
    assert narrow <= 48, f"the idle dock is still a {narrow:.0f}px pill over the page"


def test_the_run_rail_does_not_come_before_the_run_on_a_phone(ui, ui_page):
    """Below 760px there is no column for the rail: it stacks in the page flow, where an open
    one put forty `state/…json` rows between the phone and the summary the reader came for.
    """
    ui.seed_run("uir", "20260904-000353", "finished", summary="it worked")
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/run/uir:20260904-000353")
    rail = ui_page.locator(".run-view > .run-rail")
    expect(rail).to_be_visible()
    assert rail.evaluate("e => e.open") is False, "the run rail opens over the run on a phone"


#: The routes test_mobile_nav.py's NAV_ROUTES cannot carry: its list is also the count of the
#: bottom bar's destinations, so a DETAIL route cannot join it — and a detail route is exactly
#: where the widgets that widen a document live (a model picker, a transcript, an editor).
DETAIL_ROUTES = ("#/routine/uir", "#/library/rule/ask-policy", "#/run/uir:20260904-000353")


def test_no_detail_route_scrolls_sideways_on_a_phone(ui, ui_page):
    """Same invariant, one tier down: a routine page, a library document and a run view at
    390px. The bottom bar is `position: fixed` against the LAYOUT viewport, which an
    overflowing document inflates — so a page that pans sideways takes the navigation with it.
    """
    ui.seed_run("uir", "20260904-000353", "finished", summary="done")
    ui_page.set_viewport_size(PHONE)
    too_wide = {}
    for route in DETAIL_ROUTES:
        ui_page.goto(f"{ui.url}/{route}")
        ui_page.wait_for_timeout(400)          # let the view's own fetches paint
        width = _doc_width(ui_page)
        if width > PHONE["width"] + 1:
            too_wide[route] = width
    assert not too_wide, f"detail routes scroll sideways at {PHONE['width']}px: {too_wide}"
