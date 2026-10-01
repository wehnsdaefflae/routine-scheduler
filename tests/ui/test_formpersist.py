"""formpersist.js keeps a typed draft — and refuses anything credential-shaped by default.

The guard used to key on an input's TYPE and its field key alone, so a credential that arrived
in a TEXTAREA (the masked Secrets value box, the proxy sign-in paste box carrying an OAuth code
and state) was stored in the tab's sessionStorage until each field was opted out by hand.
"""

from __future__ import annotations

FIELDS = [
    # (attributes, persisted?)
    ({"placeholder": "notes for later"}, True),                     # the ordinary draft
    ({"placeholder": "paste the code the page shows"}, False),
    ({"placeholder": "value", "style": "-webkit-text-security:disc"}, False),
    ({"placeholder": "anything", "data-secret": ""}, False),
    ({"placeholder": "anything else", "autocomplete": "new-password"}, False),
    ({"aria-label": "api key"}, False),
    ({"placeholder": "kept", "data-persist": "draft-code-review"}, True),   # explicit opt-in
]


def test_credential_shaped_fields_are_never_kept_as_drafts(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/")
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate("""(fields) => {
        const box = document.createElement("div");
        box.id = "fp-probe";
        fields.forEach(([attrs], i) => {
            const t = document.createElement("textarea");
            for (const [k, v] of Object.entries(attrs)) t.setAttribute(k, v);
            t.dataset.probe = String(i);
            box.append(t);
        });
        document.getElementById("view").append(box);
    }""", FIELDS)
    for i, _ in enumerate(FIELDS):
        ui_page.locator(f'[data-probe="{i}"]').fill(f"typed-{i}")
    stored = ui_page.evaluate("() => Object.values(sessionStorage).join('|')")
    for i, (attrs, persisted) in enumerate(FIELDS):
        assert (f"typed-{i}" in stored) == persisted, (attrs, stored)
