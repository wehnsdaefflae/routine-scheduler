"""The token gate's TIER path. A browser holding the routine bearer renders the whole
console — every tab is a read — and only the first mutation fails. Before R94's marker
header that 403 became an unactionable toast with no way back to the token field: on a
phone there is no devtools to clear localStorage by hand, so the browser stayed stranded
until the operator cleared site data. The gate must re-open instead.
"""

from .conftest import ROUTINE_TOKEN, TOKEN


def test_routine_token_browser_regates_instead_of_stranding(ui, page):
    page.add_init_script(f"localStorage.setItem('rsched_token', {ROUTINE_TOKEN!r})")
    page.goto(ui.url)
    page.wait_for_selector(".topbar", timeout=15000)     # reads pass: the console renders

    # a config-mutating call — not awaited, so a gate already opened by the boot SSE ticket
    # (also a POST) cannot deadlock this evaluate on its pending promise
    page.evaluate("""() => { import('/static/api.js').then(
        (m) => m.api('/api/groups', { method: 'POST', body: { name: 'G' } }).catch(() => {})); }""")

    gate = page.locator(".token-gate")
    gate.wait_for(state="visible", timeout=15000)
    assert "routine token" in gate.inner_text()
    # the rejected credential is dropped, so a reload cannot re-strand this browser
    assert page.evaluate("localStorage.getItem('rsched_token')") is None

    # and the gate is a way back IN, not just a wall: the primary token signs in
    page.fill(".token-gate input", TOKEN)
    page.click(".token-gate button")
    gate.wait_for(state="detached", timeout=15000)
    assert page.evaluate("localStorage.getItem('rsched_token')") == TOKEN
    # The negative half — an ordinary 403 (an AUTHORIZED caller refused one resource) must
    # NOT drop a good token — is pinned server-side, where it is deterministic: api.js keys
    # strictly on the marker header, and test_api_fs asserts the plain refusals omit it.
