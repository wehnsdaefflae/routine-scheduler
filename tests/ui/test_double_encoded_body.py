"""F524 — `api()` stringifies the body, so a caller that stringifies too must be REFUSED."""


def test_a_pre_stringified_body_is_refused_with_a_teaching_error(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    out = ui_page.evaluate("""() => import('/static/api.js').then(async (m) => {
        const caught = async (body) => {
            try { await m.api('/api/status', {method: 'POST', body}); return null; }
            catch (e) { return e.message; }
        };
        return {
            // the mistake: a caller that stringified first
            object: await caught(JSON.stringify({text: 'hi'})),
            array: await caught(JSON.stringify([1, 2])),
            // NOT the mistake: a plain string is a legitimate JSON body value, and an
            // endpoint taking one must keep working
            plain: await caught('just a string'),
        };
    })""")
    msg = out["object"]
    assert msg, "a pre-stringified object body was accepted silently"
    assert "already" in msg.lower() and "api()" in msg
    assert out["array"], "a pre-stringified array body was accepted silently"
    # a genuine string body still goes through (it fails later on the network/404, not here)
    assert not (out["plain"] or "").startswith("api() serializes"), \
        "a plain string body must not be mistaken for double encoding"
