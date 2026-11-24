"""The phone's bottom navigation bar — one file for the whole contract."""

from __future__ import annotations

import json

from playwright.sync_api import expect

#: Every destination the rail is OFFERING. `:not([hidden])` is load-bearing, not tidiness:
#: 0.359.0 added a ninth anchor, `data-nav="browser"`, which ships `hidden` and is revealed
#: only when a shared browser is configured. A bare `a[data-nav]` counts it anyway, so this
#: file went red on main the day that feature landed — the count assertion was measuring the
#: markup rather than what the phone actually offers.
NAV = ".topbar nav a[data-nav]:not([hidden])"
PHONE = {"width": 390, "height": 780}

#: A token with NO break opportunity inside it — a commit sha, a run id, a base64 blob. It has to
#: be this shape: Chrome breaks a path after its `/` (UAX #14), so a long path does NOT reproduce
#: the bug and a guard written with one passes over a broken page.
LONG_TOKEN = "a9f3c1e0b7d4568291acde3f0b1729d5e8c4a6b3f9012d7e5c8a1b4f6039e2d7c5a8b1f4"

#: Every destination the rail offers, plus the run view (the surface that carries a transcript).
NAV_ROUTES = ("#/", "#/routines", "#/messages", "#/changes", "#/stats", "#/questions",
              "#/library", "#/settings", "#/help")


def _seed_wide_transcript(ui, slug: str, ts: str) -> None:
    """A run whose transcript carries unbreakable tokens in every text surface the renderer
    has: the say line, an observation body, and the finish banner's markdown.
    """
    run_dir = ui.seed_run(slug, ts, "finished", summary=f"done — wrote {LONG_TOKEN}")
    events = [
        {"type": "assistant_action", "turn": 1, "ts": "2026-09-04T00:03:53+00:00",
         "payload": {"kind": "util", "name": "codemap", "note": f"seen: {LONG_TOKEN}",
                     "say": f"Reading {LONG_TOKEN} to see what the last pass recorded."}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "util", "output": f"wrote {LONG_TOKEN}"}},
        {"type": "user_injection", "turn": 1, "payload": {"text": f"check {LONG_TOKEN} too"}},
        {"type": "error", "turn": 1, "payload": {"error": f"failed at {LONG_TOKEN}"}},
        {"type": "finish", "turn": 2, "turns": 2, "usage_total": {"in": 10, "out": 4},
         "payload": {"status": "ok", "summary": f"Report at {LONG_TOKEN}."}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _fits(ui_page, selector: str, width: int) -> int:
    """How many matching elements are displayed AND sit inside `width` CSS pixels of the
    LEFT edge of the document. `width` is the emulated device width the test set — never
    `window.innerWidth`, which the overflowing document itself inflates.
    """
    return ui_page.evaluate(
        """([sel, width]) => [...document.querySelectorAll(sel)].filter((n) => {
            const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
            return s.display !== "none" && s.visibility !== "hidden"
                && r.width > 0 && r.height > 0
                && r.left >= -1 && r.right <= width + 1;
        }).length""", [selector, width])


def test_mobile_bottom_nav_shows_every_destination_over_a_wide_transcript(ui, ui_page):
    """F439's own assertion, kept — but it is the WEAKER of the two here, and knowing why matters."""
    _seed_wide_transcript(ui, "uir", "20260904-000353")
    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/run/uir:20260904-000353")
    expect(ui_page.locator(NAV).first).to_be_visible()
    expect(ui_page.locator(NAV)).to_have_count(len(NAV_ROUTES))
    assert _fits(ui_page, NAV, PHONE["width"]) == len(NAV_ROUTES)


def test_no_route_scrolls_sideways_on_a_phone(ui, ui_page):
    """The invariant behind the bar, asserted per route so the NEXT overflowing widget is
    named by the test rather than by the operator. A route is allowed to scroll a child
    container sideways (`overflow-x: auto` on a table, a code fence); the DOCUMENT is not.
    """
    _seed_wide_transcript(ui, "uir", "20260904-000353")
    ui_page.set_viewport_size(PHONE)
    too_wide = {}
    for route in (*NAV_ROUTES, "#/run/uir:20260904-000353"):
        ui_page.goto(f"{ui.url}/{route}")
        expect(ui_page.locator(NAV).first).to_be_visible()
        ui_page.wait_for_timeout(350)   # let the view's own fetches paint
        width = ui_page.evaluate("() => document.documentElement.scrollWidth")
        if width > PHONE["width"] + 1:
            too_wide[route] = width
    assert not too_wide, f"document scrolls sideways at {PHONE['width']}px: {too_wide}"


#: A subscription with every window a Claude account can report. The chip used to print ALL of
#: them joined with " · ", which is ~120 characters of `white-space: nowrap` — the operator's
#: report on 2026-09-18: "there's only a chip for claude ... and it breaks the horizontal
#: viewport on mobile. if we have this info at the respective endpoint on the settings page,
#: that would be enough actually."
