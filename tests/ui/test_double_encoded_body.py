"""F524 — `api()` stringifies the body, so a caller that stringifies too must be REFUSED.

`api(path, {body})` does `JSON.stringify(body)` unconditionally (static/api.js). A caller
that passes `JSON.stringify({text})` therefore sends a JSON *string* where the endpoint
expects an object — and nothing anywhere notices. The request is well-formed, the transport
succeeds, and the server rejects it (or worse, accepts it) at runtime, in one flow, with an
error that points at the endpoint rather than at the caller.

122 call sites pass a body. The convention is right in all of them today; it is the NEXT one
that pays. A wrong answer the caller cannot detect is exactly what must be made loud
(`failure-visibility`), so `api()` now throws on a body that is already serialized JSON,
naming the mistake and the fix.

Evaluated as a pure function in the browser ESM context — deterministic, no network.
"""

from .conftest import TOKEN


def test_a_pre_stringified_body_is_refused_with_a_teaching_error(ui, page):
    page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
    page.goto(ui.url)
    page.wait_for_selector(".topbar", timeout=15000)
    out = page.evaluate("""() => import('/static/api.js').then(async (m) => {
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
