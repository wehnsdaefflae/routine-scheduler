"""Sending a message must not rebuild the conversation.

A conversation is ONE run resumed IN PLACE (api_conversations.message → runner.resume_terminal
→ runner.resume, same run dir, same run id), so a message changes the transcript and nothing
above it. The view nevertheless remounted the whole thing after every send: the detail read,
the head — whose rule picker GETs /api/library, and that endpoint lints the entire library on
every call — the connections card, the lineage, and the eight rail reads, plus a re-render of
the thread from offset 0 which re-fetched every attachment thumbnail. A five-message exchange
cost five full library lints and ~50 requests, while the reply's first tokens waited behind
the daemon answering them.

What a send needs instead is the one thing that actually changed: the tail had ENDED, and there
are new events after its last offset. stream.js `resume()` re-attaches there.
"""

from __future__ import annotations

from playwright.sync_api import expect

# The stub runner resumes a terminal conversation to this ts (tests/ui/conftest.py). Seeding
# the conversation's only run dir at the same ts is what makes the resume land on the run the
# view is already following — which is what the real Runner does for every conversation.
RESUMED_TS = "20260715-120001"


def _start_conversation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Plan the trip.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    return ui_page.url.rsplit("/", 1)[-1]


def test_a_send_resumes_the_tail_instead_of_remounting_the_view(ui, ui_page):
    slug = _start_conversation(ui, ui_page)
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
