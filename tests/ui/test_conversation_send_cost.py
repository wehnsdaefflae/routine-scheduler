"""Sending a message must not rebuild the conversation."""

from __future__ import annotations

from playwright.sync_api import expect

from .helpers import start_conversation

# The stub runner resumes a terminal conversation to this ts (tests/ui/conftest.py). Seeding
# the conversation's only run dir at the same ts is what makes the resume land on the run the
# view is already following — which is what the real Runner does for every conversation.
RESUMED_TS = "20260715-120001"


def test_a_send_resumes_the_tail_instead_of_remounting_the_view(ui, ui_page):
    slug, _conv_dir = start_conversation(ui, ui_page, "Plan the trip.")
    ui.seed_run(slug, RESUMED_TS, "finished", home=ui.conversations,
                summary="here is the plan")
    ui_page.reload()
    expect(ui_page.locator(".conv-composer")).to_be_visible()
    ui_page.wait_for_timeout(800)        # the head's own first loads

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if "/api/" in r.url else None)

    ui_page.locator(".conv-view textarea").last.fill("and book the train")
    ui_page.get_by_role("button", name="send").click()
    # the send landed: the echo bubble is the view's own proof of it, and a remount is
    # exactly what used to rebuild it
    expect(ui_page.locator(".msg.user.pending")).to_be_visible()
    ui_page.wait_for_timeout(2000)       # past the 700 ms re-attach

    library = [u for u in seen if u.endswith("/api/library")]
    assert not library, f"the send re-linted the whole library: {library}"
    detail = [u for u in seen if u.endswith(f"/api/conversations/{slug}")]
    assert not detail, f"the send re-read the conversation detail: {detail}"
