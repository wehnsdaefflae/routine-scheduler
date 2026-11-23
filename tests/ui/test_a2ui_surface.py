"""The A2UI surface store, exercised in the browser that will render it — D310 step 1: the six
envelope messages, JSON-Pointer binding with its two escapes, collection scopes that make a
relative path mean the item being iterated, two-way binding that writes a path the agent never
initialized, and the protocol's own refusals (a duplicate surfaceId, an update to a surface that
was never created, a message carrying no envelope key); it runs as a browser test because the
module ships to the browser and `static/` is served from the tree, so a module that parses only
under a bundler would pass a Python test and fail every real user.
"""

from __future__ import annotations

import pytest

#: The module under test is pure state, so one page load serves every case: the harness imports
#: it once and each test drives it through `window.__a2ui`.
HARNESS = """
    const mod = await import('/static/a2ui/surface.js');
    window.__a2ui = mod;
    window.__store = new mod.SurfaceStore();
"""


def _drive(page, body: str):
    """Run `body` inside the page with the module imported, and bring its result back as JSON."""
    return page.evaluate(f"async () => {{{HARNESS}\n{body}}}")


@pytest.fixture
def surface_page(ui, ui_page):
    """A signed-in console page — the module is served from `static/`, so it is imported from
    the live tree exactly as a user's browser imports it.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    return ui_page


def test_a_created_surface_binds_against_the_data_sent_with_it(surface_page):
    """One createSurface carrying both dataModel and components resolves its bindings against
    that data immediately, which the spec requires and which the other application order breaks
    for one frame."""
    out = _drive(surface_page, """
        window.__store.apply({version: "1.0", createSurface: {
            surfaceId: "s1",
            dataModel: {name: "Jane Doe"},
            components: [
                {id: "root", component: "Column", children: ["who"]},
                {id: "who", component: "Text", text: {"@path": "/name"}},
            ],
        }});
        const s = window.__store.get("s1");
        return {text: s.prop("who", "text"), root: s.root.component,
                kids: s.childrenOf("root").map(c => c.id)};
    """)
    assert out == {"text": "Jane Doe", "root": "Column", "kids": ["who"]}


def test_a_relative_path_inside_a_collection_names_the_item_being_iterated(surface_page):
    """A ChildList template creates one child scope per array item, so a path without a leading
    slash resolves inside that item while an absolute path still reaches the model root — the
    one rule that makes a list of rows possible at all."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {
            surfaceId: "s2",
            dataModel: {company: "Acme", employees: [{name: "Alice"}, {name: "Bob"}]},
            components: [
                {id: "root", component: "List",
                 children: {path: "/employees", componentId: "row"}},
                {id: "row", component: "Column", children: ["who", "where"]},
                {id: "who", component: "Text", text: {"@path": "name"}},
                {id: "where", component: "Text", text: {"@path": "/company"}},
            ],
        }});
        const s = window.__store.get("s2");
        const rows = s.childrenOf("root");
        return {
            scopes: rows.map(r => r.scope),
            names: rows.map(r => s.prop("who", "text", {scope: r.scope})),
            company: rows.map(r => s.prop("where", "text", {scope: r.scope})),
        };
    """)
    assert out == {
        "scopes": ["/employees/0", "/employees/1"],
        "names": ["Alice", "Bob"],
        "company": ["Acme", "Acme"],
    }


def test_an_input_writes_a_path_the_agent_never_initialized(surface_page):
    """Two-way binding creates the path on write — the spec's own words — so a TextField may be
    bound somewhere the data model has nothing, and a second component bound to the same path
    reads the typed value back without any message from the agent."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s3", components: [
            {id: "root", component: "Column", children: ["field", "echo"]},
            {id: "field", component: "TextField", value: {"@path": "/form/email"}},
            {id: "echo", component: "Text", text: {"@path": "/form/email"}},
        ]}});
        const s = window.__store.get("s3");
        const before = s.prop("echo", "text");
        s.write("/form/email", "jane@example.com");
        return {before, after: s.prop("echo", "text"), model: s.dataModel};
    """)
    assert out["before"] is None
    assert out["after"] == "jane@example.com"
    assert out["model"] == {"form": {"email": "jane@example.com"}}


def test_a_null_value_deletes_the_key_instead_of_storing_a_null(surface_page):
    """updateDataModel with an explicit null is how the protocol spells a REMOVAL, so the key
    must be gone rather than present and null — a renderer that stored the null would show an
    empty row where the agent removed the row."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s4", dataModel: {a: 1, b: 2}}});
        window.__store.apply({updateDataModel: {surfaceId: "s4", path: "/b", value: null}});
        const kept = window.__store.get("s4").dataModel;
        window.__store.apply({updateDataModel: {surfaceId: "s4", value: {fresh: true}}});
        return {kept, replaced: window.__store.get("s4").dataModel};
    """)
    assert out == {"kept": {"a": 1}, "replaced": {"fresh": True}}


def test_a_pointer_unescapes_its_two_escapes_in_the_right_order(surface_page):
    """RFC 6901 escapes `/` as ~1 and `~` as ~0 and fixes the order they are undone in, and the
    token `~01` is the case that tells the orders apart: undoing `~1` first leaves `~1` to be
    produced by `~0`, which is the literal key `~1` the spec means, while undoing `~0` first
    makes `~1` and then turns it into `/` — so a model carrying BOTH keys is read wrongly by an
    implementation that reverses them (`/~01` would fetch the value stored at `/`)."""
    out = _drive(surface_page, """
        const {readPointer} = window.__a2ui;
        const model = {"a/b": 1, "c~d": 2, "~1": 3, "/": 4};
        return [readPointer(model, "/a~1b"), readPointer(model, "/c~0d"),
                readPointer(model, "/~01"), readPointer(model, "/~10"),
                readPointer(model, "/missing")];
    """)
    assert out == [1, 2, 3, None, None]


def test_a_missing_binding_is_undefined_and_not_an_error(surface_page):
    """Progressive rendering: the data for a binding routinely arrives after the component that
    reads it, so an unresolved path yields undefined and renders as empty rather than throwing
    and taking the surface down."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s5", components: [
            {id: "root", component: "Text", text: {"@path": "/later/deep/value"}},
        ]}});
        const s = window.__store.get("s5");
        const missing = s.prop("root", "text");
        window.__store.apply({updateDataModel: {
            surfaceId: "s5", path: "/later", value: {deep: {value: "arrived"}}}});
        return {missing: missing === undefined, then: s.prop("root", "text"),
                text: window.__a2ui.asText(missing)};
    """)
    assert out == {"missing": True, "then": "arrived", "text": ""}


def test_values_become_text_and_booleans_the_way_the_spec_fixes_them(surface_page):
    """Two renderers must agree on what a non-string value shows and what counts as true, so the
    conversions are the spec's: objects and arrays stringify as JSON, null is empty, and a
    ValidationResult is worth its `valid` flag rather than being true for being an object."""
    out = _drive(surface_page, """
        const {asText, asBool} = window.__a2ui;
        return {
            text: [asText(3), asText(false), asText(null), asText(undefined),
                   asText({a: 1}), asText([1, 2])],
            bool: [asBool({valid: false}), asBool({valid: true}), asBool([]),
                   asBool([0]), asBool({}), asBool("x"), asBool(0)],
        };
    """)
    assert out["text"] == ["3", "false", "", "", '{"a":1}', "[1,2]"]
    assert out["bool"] == [False, True, False, True, False, True, False]


def test_the_protocol_refusals_name_what_was_wrong(surface_page):
    """Three states the protocol forbids must be refused rather than guessed at: a surfaceId
    created twice (an INTEGRITY_ERROR, since ids are unique for the renderer's lifetime), an
    update to a surface that was never created, and a message carrying no envelope key — each
    one a lost or malformed message that would otherwise render as a silently half-drawn
    surface."""
    out = _drive(surface_page, """
        const err = (fn) => { try { fn(); return "no error"; }
                              catch (e) { return `${e.name}:${e.code}`; } };
        window.__store.apply({createSurface: {surfaceId: "s6"}});
        return [
            err(() => window.__store.apply({createSurface: {surfaceId: "s6"}})),
            err(() => window.__store.apply({
                updateComponents: {surfaceId: "nope", components: []}})),
            err(() => window.__store.apply({surfaceId: "s6"})),
            err(() => window.__store.apply({
                createSurface: {surfaceId: "s7"}, deleteSurface: {surfaceId: "s6"}})),
        ];
    """)
    assert out == [
        "A2uiProtocolError:INTEGRITY_ERROR",
        "A2uiProtocolError:INTEGRITY_ERROR",
        "A2uiProtocolError:PROTOCOL_ERROR",
        "A2uiProtocolError:PROTOCOL_ERROR",
    ]


def test_a_deleted_surface_frees_its_id_for_reuse(surface_page):
    """deleteSurface is the only way to reuse an id, and it must actually free it: a renderer
    that kept the entry would refuse the agent's legitimate recreation forever."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s8", dataModel: {v: 1}}});
        window.__store.apply({deleteSurface: {surfaceId: "s8"}});
        const gone = window.__store.get("s8") === undefined;
        window.__store.apply({createSurface: {surfaceId: "s8", dataModel: {v: 2}}});
        return {gone, model: window.__store.get("s8").dataModel};
    """)
    assert out == {"gone": True, "model": {"v": 2}}


def test_every_applied_message_tells_a_subscriber_what_changed(surface_page):
    """A renderer repaints what changed instead of diffing the world, so the store announces the
    surface id and the envelope key of every message it applies — and a message it REFUSES
    announces nothing, or a listener would repaint a surface that does not exist."""
    out = _drive(surface_page, """
        const seen = [];
        window.__store.subscribe((id, key) => seen.push(`${id}:${key}`));
        window.__store.apply({createSurface: {surfaceId: "s9"}});
        window.__store.apply({updateDataModel: {surfaceId: "s9", value: {a: 1}}});
        try { window.__store.apply({updateComponents: {surfaceId: "ghost", components: []}}); }
        catch (e) { seen.push("refused"); }
        return seen;
    """)
    assert out == ["s9:createSurface", "s9:updateDataModel", "refused"]


def test_the_surface_container_itself_cannot_be_updated(surface_page):
    """The canonical Surface container always has child `root` and the spec forbids modifying it
    through updateComponents; without the refusal an agent could replace the mount point and
    orphan the whole tree."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s10"}});
        try {
            window.__store.apply({updateComponents: {surfaceId: "s10",
                components: [{id: "Surface", component: "Surface"}]}});
            return "no error";
        } catch (e) { return e.message; }
    """)
    assert "Surface container cannot be modified" in out


def test_a_function_call_property_is_handed_to_the_host(surface_page):
    """A `{"@call": …}` property is a catalog-defined function, and this module must not pretend
    to know what one MEANS: it resolves the call's arguments (which may themselves be bindings)
    and hands the name to the host, so the catalog owns the vocabulary."""
    out = _drive(surface_page, """
        window.__store.apply({createSurface: {surfaceId: "s11",
            dataModel: {price: 12},
            components: [{id: "root", component: "Text",
                          text: {"@call": "formatCurrency",
                                 args: {value: {"@path": "/price"}, code: "EUR"}}}]}});
        const s = window.__store.get("s11");
        const calls = [];
        const text = s.prop("root", "text", {callFunction: (name, args) => {
            calls.push([name, args]); return "12.00 EUR";
        }});
        const unhandled = s.prop("root", "text");
        return {text, calls, unhandled: unhandled === undefined};
    """)
    assert out["text"] == "12.00 EUR"
    assert out["calls"] == [["formatCurrency", {"value": 12, "code": "EUR"}]]
    assert out["unhandled"] is True


def test_a_pointer_refuses_to_index_an_array_by_name(surface_page):
    """A non-numeric token against an array would silently create a string key on it, which
    leaves every other binding into that list intact-looking and wrong, so it is refused where
    it happens rather than discovered later as a missing row."""
    out = _drive(surface_page, """
        const {writePointer} = window.__a2ui;
        try { writePointer({rows: [{v: 1}]}, "/rows/first/v", 2); return "no error"; }
        catch (e) { return e.name; }
    """)
    assert out == "A2uiProtocolError"


def test_the_envelope_keys_are_the_six_the_spec_defines(surface_page):
    """The dispatch table is the protocol's surface area: a key added or lost here changes what
    the renderer accepts from every agent, so the list itself is pinned."""
    out = _drive(surface_page, "return window.__a2ui.ENVELOPE_KEYS;")
    assert out == [
        "createSurface", "updateComponents", "updateDataModel",
        "deleteSurface", "callRendererFunction", "agentFunctionResponse",
    ]
