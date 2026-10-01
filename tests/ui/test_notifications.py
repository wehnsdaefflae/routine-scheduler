"""Tab-open (tier 1) notifications for open decisions — Settings → Notifications, opt-in.

Chrome on Android exposes `Notification` and lets a site be granted permission, but its
constructor throws ("Illegal constructor — use ServiceWorkerRegistration.showNotification()").
The notifier marked a decision seen, then threw at the constructor before the seen list was
stored: on that platform nothing was ever shown, and every snapshot threw again at the same
decision. It now raises the same notification through the console's service worker, whose
click handler opens the URL the notification carries.

No real OS notification can be observed from a test, so the API is stubbed before the console
loads: the page constructor records what it was asked to show (or throws, like Android's), and
the worker registration records what reaches it.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

STUB = """(() => {
  window.__shown = [];
  class FakeNotification {
    constructor(title, options) {
      if (%(throws)s) throw new TypeError("Failed to construct 'Notification': Illegal constructor.");
      window.__shown.push({ via: "page", title, ...options });
    }
    close() {}
  }
  FakeNotification.permission = "granted";
  FakeNotification.requestPermission = () => Promise.resolve("granted");
  window.Notification = FakeNotification;
  ServiceWorkerContainer.prototype.getRegistration = async () => ({
    showNotification: async (title, options) => {
      window.__shown.push({ via: "worker", title, ...options });
    },
  });
  localStorage.setItem("rsched_notify", "on");
})()"""


def _shown(page) -> list[dict]:
    return page.evaluate("window.__shown")


def _seen(page) -> list[str]:
    return json.loads(page.evaluate("localStorage.getItem('rsched_notify_seen') || '[]'"))


def test_an_open_decision_notifies_once(ui, ui_page):
    ui.seed_question("uir", "q-ship", "Ship it?")
    ui_page.add_init_script(STUB % {"throws": "false"})
    ui_page.goto(f"{ui.url}/#/help")
    expect(ui_page.locator("#q-badge")).to_have_text("1")
    ui_page.wait_for_function("window.__shown.length > 0")
    [shown] = _shown(ui_page)
    assert shown["via"] == "page"
    assert shown["title"] == "decision needed · uir"
    assert shown["body"] == "Ship it?" and shown["tag"] == "rsched-q-ship"
    assert _seen(ui_page) == ["q-ship"]

    ui_page.reload()                        # the seen list survives: no second notification
    expect(ui_page.locator("#q-badge")).to_have_text("1")
    ui_page.wait_for_timeout(500)
    assert _shown(ui_page) == []


def test_without_a_page_constructor_the_worker_raises_it(ui, ui_page):
    ui.seed_question("uir", "q-ship", "Ship it?")
    ui_page.add_init_script(STUB % {"throws": "true"})
    ui_page.goto(f"{ui.url}/#/help")
    expect(ui_page.locator("#q-badge")).to_have_text("1")
    ui_page.wait_for_function("window.__shown.length > 0")
    [shown] = _shown(ui_page)
    assert shown["via"] == "worker"
    assert shown["title"] == "decision needed · uir"
    assert shown["tag"] == "rsched-q-ship"
    # the worker's click handler opens what the notification carries (static/sw.js)
    assert shown["data"] == {"url": "/#/questions"}
    assert _seen(ui_page) == ["q-ship"]
