"""api.js `detailMessage` renders a legible one-line message from FastAPI's error `detail`
in every shape it arrives in — a plain string for our HTTPExceptions, but a LIST of
{loc, msg, type} validation records for a 422 (an unknown / forbidden request field).
`new Error(list)` stringifies to "[object Object]"; before F392 that was the opaque toast the
Decisions page's rejected config-patch apply showed (a `{"rules": [...]}` patch hit
RoutinePatch's extra=forbid). Evaluated as a pure function in the browser ESM context —
deterministic, no network, no timing.
"""

from .conftest import TOKEN


def test_detail_message_renders_a_422_validation_list_legibly(ui, page):
    page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
    page.goto(ui.url)
    page.wait_for_selector(".topbar", timeout=15000)
    out = page.evaluate("""() => import('/static/api.js').then((m) => ({
        string: m.detailMessage('keep_runs must be a positive integer'),
        list: m.detailMessage([{loc: ['body', 'rules'], msg: 'extra fields not permitted',
                                type: 'extra_forbidden'}]),
        multi: m.detailMessage([{loc: ['body', 'a'], msg: 'bad a'},
                                {loc: ['body', 'b'], msg: 'bad b'}]),
        bareString: m.detailMessage(['just a string']),
        empty: m.detailMessage(undefined),
    }))""")
    # a string detail passes straight through
    assert out["string"] == "keep_runs must be a positive integer"
    # a 422 validation record renders as "field: message", NEVER "[object Object]"
    assert out["list"] == "rules: extra fields not permitted"
    assert "[object Object]" not in out["list"]
    # several records join legibly
    assert out["multi"] == "a: bad a; b: bad b"
    # a list of bare strings is kept verbatim
    assert out["bareString"] == "just a string"
    # nothing to say stays empty (caller falls back to the status line)
    assert out["empty"] == ""
