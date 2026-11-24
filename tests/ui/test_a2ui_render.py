"""The A2UI renderer CORE — D310 step 3: what the layer between the surface store and the widgets
owns, exercised in the browser it ships to. Catalog resolution (which the spec forbids falling
back on), an undrawable component replaced in place rather than failing the surface, collection
scopes through the walk, `@accessibility` mapped to ARIA, `weight` applied by the parent, the
outward action channel, and the repaint that must not eat the caret — the widgets' own coverage is
`test_a2ui_widgets.py`, which imports this module's harness. It is a browser test rather than a
Python one because the modules are served straight from `static/` with no bundler, so a module
that parses only under a build step would pass a unit test and fail every real user.
"""

from __future__ import annotations

import pytest

#: Every layer imported the way a view imports them: the widget modules register themselves into
#: the core's registry at import time, so importing them IS the renderer's configuration.
HARNESS = """
    const surface = await import('/static/a2ui/surface.js');
    const render = await import('/static/a2ui/render.js');
    await import('/static/a2ui/widgets-layout.js');
    await import('/static/a2ui/widgets-input.js');
    window.__a2 = {...surface, ...render};
    window.__store = new surface.SurfaceStore();
    window.__events = [];
    window.__host = document.createElement('div');
    window.__host.id = 'a2-test-host';
    document.body.append(window.__host);
    window.__mount = (id) => render.mountSurface(window.__host, window.__store, id,
        {onEvent: (e) => window.__events.push(e)});
"""

#: The Basic Catalog's own id, as the spec publishes it — the surface default in every case here.
BASIC = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"


def drive(page, body: str):
    """Run `body` in the page with every layer imported, and bring its result back as JSON."""
    return page.evaluate(f"async () => {{{HARNESS}\n{body}}}")


def a2ui_page(ui, ui_page):
    """A signed-in console page, ready to import the modules — they are served from `static/`, so
    they load exactly as a user's browser loads them.

    A BUILDER rather than a fixture, which is this repo's own answer to the same problem
    `tests/test_backup_snapshots.py` states for `share_root`: the sibling module needs the same
    setup, and IMPORTING a fixture makes every test that takes it read as a redefinition (ruff
    F811). A plain function is imported without that cost.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    return ui_page


@pytest.fixture
def render_page(ui, ui_page):
    """This module's own page, from the shared builder."""
    return a2ui_page(ui, ui_page)


def test_a_component_resolving_to_no_catalog_is_refused_rather_than_guessed(render_page):
    """The spec forbids falling back to the renderer's only registered catalog, in those words,
    and this is the step where that matters: a component that resolves by luck on a
    single-catalog renderer breaks the moment a second catalog exists, which is exactly what this
    campaign adds."""
    out = drive(render_page, """
        window.__store.apply({createSurface: {surfaceId: "nocat", components: [
            {id: "root", component: "Text", text: "orphan"}]}});
        window.__mount("nocat");
        const node = window.__host.querySelector('.a2-error');
        return {code: node.dataset.code, message: node.textContent};
    """)
    assert out["code"] == "CATALOG_ERROR"
    assert "no @catalogId" in out["message"]


def test_a_component_the_catalog_does_not_declare_says_so_in_place(render_page):
    """Progressive rendering means half a surface is normal, so one undrawable component must not
    fail the rest: the error takes that component's place and the surface around it stands."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "unknown", catalogId: "{BASIC}",
            components: [
                {{id: "root", component: "Column", children: ["good", "bad"]}},
                {{id: "good", component: "Text", text: "still here"}},
                {{id: "bad", component: "Hologram"}},
            ]}}}});
        window.__mount("unknown");
        return {{
            error: window.__host.querySelector('.a2-error').dataset.code,
            sibling: window.__host.querySelector('.a2-text').textContent,
        }};
    """)
    assert out["error"] == "UNKNOWN_COMPONENT"
    assert out["sibling"] == "still here", "one undrawable component took the whole surface down"


def test_a_collection_template_draws_one_node_per_item_in_its_own_scope(render_page):
    """The ChildList template is what makes a list of rows possible: one instantiation per array
    item, each in the scope of that item, so a relative binding inside the template means that
    item's field while an absolute one still reaches the model root."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "rows", catalogId: "{BASIC}",
            dataModel: {{team: "Acme", people: [{{name: "Alice"}}, {{name: "Bob"}}]}},
            components: [
                {{id: "root", component: "List",
                  children: {{path: "/people", componentId: "row"}}}},
                {{id: "row", component: "Row", children: ["who", "where"]}},
                {{id: "who", component: "Text", text: {{"@path": "name"}}}},
                {{id: "where", component: "Text", text: {{"@path": "/team"}}}},
            ]}}}});
        window.__mount("rows");
        return [...window.__host.querySelectorAll('.a2-row')].map(
            r => [...r.querySelectorAll('.a2-text')].map(t => t.textContent));
    """)
    assert out == [["Alice", "Acme"], ["Bob", "Acme"]]


def test_a_button_sends_its_event_with_the_context_resolved(render_page):
    """An agent action carries `context` whose values may be bindings, and the agent needs the
    VALUES — a renderer forwarding `{"@path": "/id"}` would make every agent implement the data
    model a second time. `userMessage` is resolved to a human-readable string the same way."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "act", catalogId: "{BASIC}",
            dataModel: {{item: "123"}},
            components: [
                {{id: "root", component: "Button", child: "label", action: {{event: {{
                    name: "submit_form",
                    userMessage: {{"@call": "formatString",
                                  args: {{value: "Submitted #${{/item}}"}}}},
                    context: {{itemId: {{"@path": "/item"}}}}}}}}}},
                {{id: "label", component: "Text", text: "Go"}},
            ]}}}});
        window.__mount("act");
        window.__host.querySelector('button.a2-button').click();
        return window.__events;
    """)
    assert out == [{
        "surfaceId": "act", "name": "submit_form",
        "userMessage": "Submitted #123", "context": {"itemId": "123"},
    }]


def test_openurl_refuses_a_passive_binding_and_runs_from_a_gesture(render_page):
    """`openUrl` declares `requiresUserActivation: true`, so the spec requires the renderer to
    BLOCK it in a passive property binding (reporting EXPRESSION_ERROR) and to run it from a
    component's action — which is the difference between a catalog function and arbitrary code."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "url", catalogId: "{BASIC}",
            components: [
                {{id: "root", component: "Column", children: ["passive", "btn"]}},
                {{id: "passive", component: "Text",
                  text: {{"@call": "openUrl", args: {{url: "https://example.org"}}}}}},
                {{id: "btn", component: "Button", child: "label", action: {{functionCall: {{
                    "@call": "openUrl", args: {{url: "https://example.org"}}}}}}}},
                {{id: "label", component: "Text", text: "Open"}},
            ]}}}});
        window.__mount("url");
        const blocked = window.__host.querySelector('.a2-error')?.dataset.code;
        const opened = [];
        const mod = await import('/static/a2ui/functions.js');
        const result = mod.callBasicFunction("openUrl", {{url: "https://example.org"}},
                                             {{activated: true, openUrl: (u) => opened.push(u)}});
        return {{blocked, opened, result: result === undefined}};
    """)
    assert out["blocked"] == "EXPRESSION_ERROR", "a passive openUrl binding was not blocked"
    assert out["opened"] == ["https://example.org"]
    assert out["result"] is True, "openUrl declares no return value"


def test_a_repaint_keeps_the_caret_where_the_user_left_it(render_page):
    """Every keystroke writes the model, which repaints the surface, so a renderer that rebuilt
    the DOM without restoring focus would make a bound text field impossible to type in — the
    defect would look like "the field only takes one character"."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "caret", catalogId: "{BASIC}",
            dataModel: {{who: ""}},
            components: [{{id: "root", component: "TextField", value: {{"@path": "/who"}}}}]}}}});
        window.__mount("caret");
        let input = window.__host.querySelector('input.a2-input');
        input.focus();
        for (const ch of "abc") {{
            input = window.__host.querySelector('input.a2-input');
            input.value += ch;
            input.setSelectionRange(input.value.length, input.value.length);
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
        }}
        const after = window.__host.querySelector('input.a2-input');
        return {{model: window.__store.get("caret").dataModel.who,
                focused: document.activeElement === after,
                caret: after.selectionStart}};
    """)
    assert out["model"] == "abc", "typing three characters did not reach the model"
    assert out["focused"] is True, "the repaint dropped focus, so the field takes one keystroke"
    assert out["caret"] == 3


def test_the_scheduler_catalog_registers_beside_the_basic_one(render_page):
    """MIXABLE CATALOGS are the mechanism the operator's `a-rich-plus` answer rests on: the
    extension catalog is not a replacement, so both ids must be supported at once and a component
    must resolve by its own `@catalogId` rather than by which registry was loaded last."""
    # `window.__a2` is what the harness exposes; re-importing the module here would re-declare
    # the `render` binding the harness already made, which is a SyntaxError in one page script.
    out = drive(render_page, """
        return {
            supported: window.__a2.supportedCatalogIds(),
            basic: window.__a2.BASIC_CATALOG_ID,
            scheduler: window.__a2.SCHEDULER_CATALOG_ID,
        };
    """)
    assert out["basic"] in out["supported"]
    assert out["basic"] != out["scheduler"], "the extension catalog claims the protocol's own id"


def test_accessibility_intent_becomes_aria_on_the_drawn_node(render_page):
    """A catalog carries the INTENT (a label, a role) rather than ARIA attribute names, so the
    mapping is the renderer's — once, for every component of every catalog, which is why it is in
    the core and not in each widget."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "a11y", catalogId: "{BASIC}",
            dataModel: {{who: "Ada Lovelace"}},
            components: [{{id: "root", component: "Text", text: "AL",
                "@accessibility": {{label: {{"@path": "/who"}}, role: "note",
                                   description: "the author"}}}}]}}}});
        window.__mount("a11y");
        const node = window.__host.querySelector('.a2-text');
        return {{label: node.getAttribute('aria-label'), role: node.getAttribute('role'),
                described: node.getAttribute('aria-description')}};
    """)
    assert out == {"label": "Ada Lovelace", "role": "note", "described": "the author"}


def test_weight_is_applied_by_the_parent_rather_than_by_each_widget(render_page):
    """`weight` is meaningful only on a direct child of a Row or Column and means flex-grow, so it
    belongs to the walk: a widget that had to know whether its parent is a flex container would be
    a widget that can forget."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "w", catalogId: "{BASIC}",
            components: [
                {{id: "root", component: "Row", children: ["a", "b"]}},
                {{id: "a", component: "Text", text: "wide", weight: 3}},
                {{id: "b", component: "Text", text: "narrow"}},
            ]}}}});
        window.__mount("w");
        return [...window.__host.querySelectorAll('.a2-text')].map(n => n.style.flexGrow);
    """)
    assert out == ["3", ""]


def test_a_surface_whose_root_has_not_arrived_yet_draws_nothing(render_page):
    """Progressive rendering again, from the other end: a `createSurface` may legitimately arrive
    before the component with id `root`, and the renderer must wait rather than report an error —
    an error here would make every streamed surface flash a failure first."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "early", catalogId: "{BASIC}",
            components: [{{id: "later", component: "Text", text: "not the root"}}]}}}});
        window.__mount("early");
        const before = window.__host.querySelector('.a2-surface').children.length;
        window.__store.apply({{updateComponents: {{surfaceId: "early", components: [
            {{id: "root", component: "Text", text: "here now"}}]}}}});
        return {{before, after: window.__host.querySelector('.a2-text').textContent}};
    """)
    assert out["before"] == 0, "a surface without its root drew something"
    assert out["after"] == "here now", "the root arriving did not repaint the surface"
