"""A notification click reaches the decision whichever console windows are open.

sw.js's click handler took ANY window (`includeUncontrolled: true`) and called navigate() on
it — which a window the worker does not control rejects, unhandled: the click focused a tab
and never reached the decision. The handler is exercised here in the page, against a stand-in
worker scope, because a real notification click cannot be driven over CDP: the shipped sw.js
text is evaluated with `self` bound to a fake that records what the handler asked for.
"""

from __future__ import annotations

import pytest

HARNESS = """async ({ controlled, uncontrolled, navigateFails }) => {
  const src = await (await fetch('/sw.js')).text();
  const calls = [];
  const win = (name, isControlled) => ({
    focus: async () => { calls.push(`focus ${name}`); },
    navigate: async (url) => {
      if (!isControlled || navigateFails) throw new TypeError(`${name} is not controlled`);
      calls.push(`navigate ${name} ${url}`);
    },
  });
  const handlers = {};
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    clients: {
      matchAll: async (opts) => [
        ...(controlled ? [win('controlled', true)] : []),
        ...(uncontrolled && opts.includeUncontrolled ? [win('uncontrolled', false)] : []),
      ],
      openWindow: async (url) => { calls.push(`open ${url}`); },
      claim: async () => {},
    },
    registration: {},
    skipWaiting: () => {},
  };
  new Function('self', src)(self);
  let pending;
  handlers.notificationclick({ notification: { close() {}, data: { url: '/#/questions' } },
                               waitUntil: (p) => { pending = p; } });
  try { await pending; } catch (err) { calls.push(`rejected ${err.message}`); }
  return calls;
}"""

CASES = [
    ({"controlled": True, "uncontrolled": True, "navigateFails": False},
     ["focus controlled", "navigate controlled /#/questions"]),
    ({"controlled": False, "uncontrolled": True, "navigateFails": False},
     ["open /#/questions"]),
    ({"controlled": True, "uncontrolled": False, "navigateFails": True},
     ["focus controlled", "open /#/questions"]),
]


@pytest.mark.parametrize(("windows", "expected"), CASES)
def test_a_notification_click_lands_on_its_url(ui, ui_page, windows, expected):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    assert ui_page.evaluate(HARNESS, windows) == expected
