"""A real decision ANSWERED through the A2UI surface — D310 step 4 in the browser: the console
fetches the served `createSurface` envelope, the renderer draws it from the Basic Catalog, a chip
and a typed sentence compose the answer through two-way binding, the submit's agent EVENT is
routed to the one answer route every other surface posts through, and the surface is DELETED when
the answer lands. This is what makes the protocol exercised by the console rather than only by its
own unit tests, which is the whole difference between step 3 and step 4.
"""

from __future__ import annotations

import pytest

#: The console module under test, imported the way the page imports it — importing it pulls in
#: the renderer and both widget modules, which register the catalogs as they load.
HARNESS = """
    const mod = await import('/static/components/a2ui-decision.js');
    window.__a2d = mod;
    window.__host = document.createElement('div');
    window.__host.id = 'a2d-test-host';
    document.body.append(window.__host);
"""


@pytest.fixture
def served_page(ui, ui_page):
    """A signed-in console page. The modules are served from `static/` and the envelope comes
    from the live route, so this exercises the whole path rather than a stubbed middle."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    return ui_page


def test_the_answer_body_prefers_the_pick_and_appends_the_prose(served_page):
    """The composer's picker is typed `DynamicStringList` even when the variant is mutually
    exclusive, so the model always holds an ARRAY and the first entry is the choice. A reader who
    chose an option AND wrote a sentence meant both, and the answer route takes one `text`."""
    out = served_page.evaluate(f"""async () => {{{HARNESS}
        const {{answerBody}} = window.__a2d;
        return {{
            pick: answerBody({{picked: ["b — upstream SDK"], text: ""}}),
            both: answerBody({{picked: ["a"], text: "but measure it first"}}),
            prose: answerBody({{picked: [], text: "   just words   "}}),
            empty: answerBody({{picked: [], text: "  "}}),
            absent: answerBody(undefined),
        }};
    }}""")
    assert out["pick"] == {"text": "b — upstream SDK"}
    assert out["both"] == {"text": "a — but measure it first"}
    assert out["prose"] == {"text": "just words"}
    assert out["empty"] == {"text": ""}, "an empty answer must not become a non-empty one"
    assert out["absent"] == {"text": ""}, "a missing context must not throw inside an event"


def test_a_served_decision_renders_through_the_protocol_and_answers_the_real_route(
        served_page, ui):
    """THE END-TO-END CLAIM, and the only test that makes it. Nothing is stubbed: the record is
    seeded on the harness's real routine (`decisions_read.find_question` walks only dirs carrying
    a routine.yaml, so a bare slug would 404), the envelope comes from
    `GET /api/questions/{qid}/a2ui`, the renderer draws it, the chip and the field write the data
    model, and the submit's event reaches `POST /api/questions/{qid}/answer` with the composed
    answer — the same route and body the console's own card posts."""
    qid = "q-20261124-150000-1"
    ui.seed_question("uir", qid, "Which route for the renderer?",
                     options=["a — hand-written", "b — upstream SDK"],
                     default="a — hand-written")
    served_page.evaluate(f"""async () => {{{HARNESS}
        window.__host.append(window.__a2d.a2uiDecision("{qid}"));
    }}""")
    # The envelope is fetched, so this WAITS for the drawn tree rather than assuming it: a
    # decisions page draws many cards and none of them blocks on a request.
    served_page.wait_for_selector("#a2d-test-host .a2-chip")
    drawn = served_page.evaluate("""() => {
        const host = document.querySelector('#a2d-test-host');
        return {
            errors: [...host.querySelectorAll('.a2-error')].map(n => n.textContent),
            heading: host.querySelector('.a2-text-heading')?.textContent,
            texts: [...host.querySelectorAll('.a2-text')].map(n => n.textContent),
            chips: [...host.querySelectorAll('.a2-chip')].map(n => n.textContent),
            field: Boolean(host.querySelector('textarea.a2-input')),
            modal: Boolean(host.querySelector('dialog.a2-dialog')),
        };
    }""")
    assert drawn["errors"] == [], f"the served surface did not render: {drawn['errors']}"
    assert drawn["chips"] == ["a — hand-written", "b — upstream SDK"], (
        "the options are not chips — this is the element the operator's answer asked for")
    assert "Which route for the renderer?" in drawn["texts"]
    assert drawn["heading"] == "uir is asking", (
        "the heading's formatString did not resolve the asking routine")
    assert drawn["field"] is True, "no long-text field: there would be no way to say anything"
    assert drawn["modal"] is True, "no modal for the without-an-answer sentence"

    with served_page.expect_request(
            lambda r: r.method == "POST"
            and r.url.endswith(f"/api/questions/{qid}/answer")) as sent:
        served_page.evaluate("""() => {
            const host = document.querySelector('#a2d-test-host');
            host.querySelectorAll('.a2-chip')[1].click();
            const field = host.querySelector('textarea.a2-input');
            field.value = 'but measure the build step first';
            field.dispatchEvent(new Event('input', {bubbles: true}));
            host.querySelector('button.a2-button-primary').click();
        }""")
    assert sent.value.post_data_json == {
        "text": "b — upstream SDK — but measure the build step first"}, sent.value.post_data_json

    # And the surface is GONE: a decision that has been answered is not a decision, and
    # `deleteSurface` is the protocol's own way to say so.
    served_page.wait_for_selector("#a2d-test-host .a2-answered")
    left = served_page.evaluate(
        "() => document.querySelectorAll('#a2d-test-host .a2-chip').length")
    assert left == 0, "the answered surface was left on the page"


def test_a_qid_with_no_record_says_so_in_place_rather_than_drawing_an_empty_frame(served_page):
    """A fetch that fails has to be visible: an empty frame is the one state a reader cannot tell
    from "there is no decision here", and a decisions page that silently drew nothing for a
    missing record would hide a routing defect indefinitely."""
    served_page.evaluate(f"""async () => {{{HARNESS}
        window.__host.append(window.__a2d.a2uiDecision("q-does-not-exist"));
    }}""")
    served_page.wait_for_selector("#a2d-test-host .a2-error")
    code = served_page.evaluate(
        "() => document.querySelector('#a2d-test-host .a2-error').dataset.code")
    assert code == "FETCH_ERROR"


def test_an_event_this_host_does_not_handle_is_reported_rather_than_swallowed(served_page):
    """An agent naming an event the host has no handler for is a protocol mismatch, and the
    surface saying so is how it gets found: swallowing it is how a decision looks answered and is
    not. Driven through the exported handler factory, because the surface the route composes only
    ever sends `answer_decision` — a path reachable no other way is why that factory is
    exported."""
    out = served_page.evaluate(f"""async () => {{{HARNESS}
        const surface = await import('/static/a2ui/surface.js');
        const store = new surface.SurfaceStore();
        const posted = [];
        const onEvent = window.__a2d.decisionEvents("q-x", {{
            host: window.__host, store, post: (body) => {{ posted.push(body); }},
        }});
        await onEvent({{name: "something_else", context: {{text: "hello"}}}});
        const after_unknown = {{
            code: window.__host.querySelector('.a2-error')?.dataset.code,
            posted: posted.length,
        }};
        // And the handled event DOES post, through the injected call — so the refusal above is
        // about the NAME and not about a handler that posts nothing at all.
        await onEvent({{name: "answer_decision", context: {{picked: [], text: "yes"}}}});
        return {{...after_unknown, handled: posted}};
    }}""")
    assert out["code"] == "UNKNOWN_EVENT"
    assert out["posted"] == 0, "an unhandled event posted an answer"
    assert out["handled"] == [{"text": "yes"}]


def test_an_empty_answer_is_not_posted_at_all(served_page):
    """The field's own `@checks` already say so on the surface, so the handler must not turn an
    empty composition into an empty answer — the answer route rejects one with a 400, and a
    reader would see a failure toast for a button he was told not to press yet."""
    out = served_page.evaluate(f"""async () => {{{HARNESS}
        const surface = await import('/static/a2ui/surface.js');
        const store = new surface.SurfaceStore();
        const posted = [];
        const onEvent = window.__a2d.decisionEvents("q-y", {{
            host: window.__host, store, post: (body) => {{ posted.push(body); }},
        }});
        await onEvent({{name: "answer_decision", context: {{picked: [], text: "   "}}}});
        return {{posted: posted.length,
                errors: window.__host.querySelectorAll('.a2-error').length}};
    }}""")
    assert out == {"posted": 0, "errors": 0}
