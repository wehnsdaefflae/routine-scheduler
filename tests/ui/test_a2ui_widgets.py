"""The Basic Catalog's EIGHTEEN COMPONENTS as drawn DOM — D310 step 3, the widgets' own coverage,
beside `test_a2ui_render.py` which pins what the core owns and whose harness this imports: all
eighteen mounted in one surface with no renderer error, the five interaction elements this console
has no equivalent for today (Tabs, Modal, ChoicePicker, Slider, DateTimeInput) driven as a user
drives them, a keystroke that writes the model and sends nothing, an input over a literal that is
read-only rather than broken, and a failing `@checks` rule shown where a user reads it.
"""

from __future__ import annotations

import pytest

from .test_a2ui_render import BASIC, a2ui_page, drive


@pytest.fixture
def render_page(ui, ui_page):
    """This module's own page, from the sibling's shared builder. The BUILDER is imported and the
    fixture is declared here, rather than importing the fixture itself: a fixture crossing a
    module boundary makes every test that takes it read as a redefinition (ruff F811), which is
    the same reason `tests/test_backup_snapshots.py` hands out `share_root` as a builder."""
    return a2ui_page(ui, ui_page)


def test_every_basic_catalog_component_renders_a_node(render_page):
    """All EIGHTEEN components of the Basic Catalog, mounted in one surface: the operator chose
    the full catalog ("We shouldn't be fixated on the controls we already use"), so the claim
    being pinned is coverage — a component the registry lacks draws the renderer's own error node,
    and this reads the DOM rather than the registry so a widget that throws is caught as well."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{
            surfaceId: "all", catalogId: "{BASIC}",
            dataModel: {{name: "Ada", on: true, pick: ["b"], n: 4, when: "2026-11-24"}},
            components: [
                {{id: "root", component: "Column", children: [
                    "t", "img", "ic", "vid", "aud", "div", "row", "list", "card", "tabs",
                    "modal", "btn", "tf", "cb", "cp", "sl", "dt"]}},
                {{id: "t", component: "Text", text: {{"@path": "/name"}}}},
                {{id: "img", component: "Image", url: "/static/favicon.png", description: "a dot"}},
                {{id: "ic", component: "Icon", name: "check"}},
                {{id: "vid", component: "Video", url: "/static/none.mp4"}},
                {{id: "aud", component: "AudioPlayer", url: "/static/none.mp3"}},
                {{id: "div", component: "Divider"}},
                {{id: "row", component: "Row", children: ["t2"]}},
                {{id: "t2", component: "Text", text: "in a row"}},
                {{id: "list", component: "List", children: ["t3"]}},
                {{id: "t3", component: "Text", text: "in a list"}},
                {{id: "card", component: "Card", child: "t4"}},
                {{id: "t4", component: "Text", text: "in a card"}},
                {{id: "tabs", component: "Tabs", tabs: [
                    {{title: "One", child: "t5"}}, {{title: "Two", child: "t6"}}]}},
                {{id: "t5", component: "Text", text: "first tab"}},
                {{id: "t6", component: "Text", text: "second tab"}},
                {{id: "modal", component: "Modal", trigger: "t7", content: "t8"}},
                {{id: "t7", component: "Text", text: "open"}},
                {{id: "t8", component: "Text", text: "inside the modal"}},
                {{id: "btn", component: "Button", child: "t9"}},
                {{id: "t9", component: "Text", text: "Submit"}},
                {{id: "tf", component: "TextField", label: "Name", value: {{"@path": "/name"}}}},
                {{id: "cb", component: "CheckBox", label: "On", value: {{"@path": "/on"}}}},
                {{id: "cp", component: "ChoicePicker", label: "Pick",
                  options: [{{value: "a", label: "Aye"}}, {{value: "b", label: "Bee"}}],
                  value: {{"@path": "/pick"}}}},
                {{id: "sl", component: "Slider", label: "How many", min: 0, max: 10,
                  value: {{"@path": "/n"}}}},
                {{id: "dt", component: "DateTimeInput", label: "When",
                  value: {{"@path": "/when"}}}},
            ],
        }}}});
        window.__mount("all");
        const host = window.__host;
        return {{
            errors: [...host.querySelectorAll('.a2-error')].map(n => n.textContent),
            text: host.querySelector('.a2-text').textContent,
            img: host.querySelector('img.a2-image').alt,
            icon: host.querySelector('.a2-icon').dataset.icon,
            video: Boolean(host.querySelector('video.a2-video')),
            audio: Boolean(host.querySelector('audio.a2-audio')),
            divider: Boolean(host.querySelector('.a2-divider-horizontal')),
            row: Boolean(host.querySelector('.a2-row')),
            list: Boolean(host.querySelector('.a2-list')),
            card: host.querySelector('.a2-card').textContent,
            tabs: [...host.querySelectorAll('.a2-tab')].map(n => n.textContent),
            panel: host.querySelector('.a2-tabpanel').textContent,
            dialog: Boolean(host.querySelector('dialog.a2-dialog')),
            button: host.querySelector('button.a2-button').textContent,
            field: host.querySelector('input.a2-input').value,
            check: host.querySelector('input.a2-checkbox').checked,
            choices: [...host.querySelectorAll('.a2-choice .a2-label')].map(n => n.textContent),
            slider: host.querySelector('input.a2-slider').value,
            datetime: host.querySelector('input.a2-datetime').type,
        }};
    """)
    assert out["errors"] == [], f"components the renderer could not draw: {out['errors']}"
    assert out["text"] == "Ada"
    assert out["img"] == "a dot"
    assert out["icon"] == "check"
    assert out["video"] and out["audio"] and out["divider"] and out["row"] and out["list"]
    assert out["card"] == "in a card"
    assert out["tabs"] == ["One", "Two"]
    assert out["panel"] == "first tab", "a Tabs component shows its selected tab's child"
    assert out["dialog"] is True
    assert out["button"] == "Submit"
    assert out["field"] == "Ada"
    assert out["check"] is True
    assert out["choices"] == ["Aye", "Bee"]
    assert out["slider"] == "4"
    assert out["datetime"] == "date"


def test_typing_in_a_field_writes_the_data_model_and_sends_nothing(render_page):
    """Two-way binding is LOCAL — the spec is explicit that a keystroke sends nothing to the agent
    and the value travels only when an action carries it — so this asserts both halves: the model
    changed, and the outward channel stayed empty."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "typing", catalogId: "{BASIC}",
            dataModel: {{}},
            components: [{{id: "root", component: "TextField", label: "Name",
                          value: {{"@path": "/who"}}}}]}}}});
        window.__mount("typing");
        const input = window.__host.querySelector('input.a2-input');
        input.value = "Grace";
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        return {{model: window.__store.get("typing").dataModel, sent: window.__events.length}};
    """)
    assert out["model"] == {"who": "Grace"}
    assert out["sent"] == 0, "a keystroke reached the agent, which the spec forbids"


def test_an_input_bound_to_a_literal_is_read_only_rather_than_broken(render_page):
    """A property the agent wrote as a literal has no path to write back to. Accepting keystrokes
    that vanish on the next repaint is the worse failure, so the field shows the value and refuses
    to edit it."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "lit", catalogId: "{BASIC}",
            components: [{{id: "root", component: "TextField", value: "fixed"}}]}}}});
        window.__mount("lit");
        const input = window.__host.querySelector('input.a2-input');
        return {{value: input.value, readOnly: input.readOnly}};
    """)
    assert out == {"value": "fixed", "readOnly": True}


def test_a_failing_check_is_shown_where_the_user_reads_it(render_page):
    """`@checks` are evaluated on the renderer and the message belongs beside the field, not in a
    console log: one element, one class, so a wrong field looks the same whichever catalog drew
    it, and `aria-invalid` carries it to a screen reader. WHICH message is the spec's own rule and
    it runs the way round that is easy to get backwards — a validation function's own message
    WINS, and `CheckRule.message` is the FALLBACK used only when the result omits one (§"Dynamic
    Boolean and CheckRule Coercion", rule 2, third bullet). Note for whoever reads the spec next:
    its later summary section states the same rule in reverse, and this test follows the
    normative coercion section rather than the summary."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "checks", catalogId: "{BASIC}",
            dataModel: {{mail: "not-an-address", agreed: false}},
            components: [
                {{id: "root", component: "Column", children: ["mail", "agree"]}},
                {{id: "mail", component: "TextField", label: "Email",
                  value: {{"@path": "/mail"}},
                  "@checks": [{{condition: {{"@call": "email",
                                            args: {{value: {{"@path": "/mail"}}}}}},
                               message: "the rule's own fallback"}}]}},
                {{id: "agree", component: "CheckBox", label: "Agree",
                  value: {{"@path": "/agreed"}},
                  "@checks": [{{condition: {{"@path": "/agreed"}},
                               message: "you have to agree first"}}]}},
            ]}}}});
        window.__mount("checks");
        return {{
            messages: [...window.__host.querySelectorAll('.a2-invalid')].map(n => n.textContent),
            invalid: window.__host.querySelector('input.a2-input').getAttribute('aria-invalid'),
        }};
    """)
    # The function's own message for the email field; the rule's message for the plain boolean
    # condition, which carries none of its own — both halves, so neither direction can rot.
    assert out["messages"] == ["not an email address", "you have to agree first"]
    assert out["invalid"] == "true"


def test_a_chip_picker_selects_and_a_slider_writes_a_number(render_page):
    """The two most visibly NEW elements, driven as a user drives them: a chip toggles its pressed
    state and writes the selection as a LIST (the catalog's `value` is a DynamicStringList even
    when the variant is mutually exclusive, so an agent reads one shape), and a slider writes a
    NUMBER rather than the string the DOM gives."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "chips", catalogId: "{BASIC}",
            dataModel: {{pick: [], n: 2}},
            components: [
                {{id: "root", component: "Column", children: ["cp", "sl"]}},
                {{id: "cp", component: "ChoicePicker", displayStyle: "chips",
                  variant: "mutuallyExclusive",
                  options: [{{value: "a", label: "Aye"}}, {{value: "b", label: "Bee"}}],
                  value: {{"@path": "/pick"}}}},
                {{id: "sl", component: "Slider", min: 0, max: 10, value: {{"@path": "/n"}}}},
            ]}}}});
        window.__mount("chips");
        // BOTH chips, in order: clicking one from an empty selection would leave "mutually
        // exclusive" untested (clearing an empty set is a no-op), so the second click is what
        // proves the first choice was REPLACED rather than added to. Measured red-first
        // 2026-11-24: with one click the guard stayed green through its own neuter.
        window.__host.querySelectorAll('.a2-chip')[0].click();
        window.__host.querySelectorAll('.a2-chip')[1].click();
        const slider = window.__host.querySelector('input.a2-slider');
        slider.value = "7";
        slider.dispatchEvent(new Event('input', {{bubbles: true}}));
        const model = window.__store.get("chips").dataModel;
        return {{pick: model.pick, n: model.n, numeric: typeof model.n,
                pressed: [...window.__host.querySelectorAll('.a2-chip')].map(
                    c => c.getAttribute('aria-pressed'))}};
    """)
    assert out["pick"] == ["b"]
    assert out["n"] == 7
    assert out["numeric"] == "number", "a slider wrote its value as a string"
    assert out["pressed"] == ["false", "true"]


def test_a_tab_switches_to_its_own_child_without_an_agent_round_trip(render_page):
    """Tabs are new here, and the catalog gives the selection no data path — so it is
    renderer-local state by construction. Clicking the second tab must show its child and nothing
    must be sent: a tab that needed the agent would make the whole component useless offline."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "tabs", catalogId: "{BASIC}",
            components: [
                {{id: "root", component: "Tabs", tabs: [
                    {{title: "First", child: "a"}}, {{title: "Second", child: "b"}}]}},
                {{id: "a", component: "Text", text: "page one"}},
                {{id: "b", component: "Text", text: "page two"}},
            ]}}}});
        window.__mount("tabs");
        const before = window.__host.querySelector('.a2-tabpanel').textContent;
        window.__host.querySelectorAll('.a2-tab')[1].click();
        const after = window.__host.querySelector('.a2-tabpanel').textContent;
        return {{before, after, sent: window.__events.length,
                selected: [...window.__host.querySelectorAll('.a2-tab')].map(
                    t => t.getAttribute('aria-selected'))}};
    """)
    assert out["before"] == "page one"
    assert out["after"] == "page two", "clicking a tab did not change the panel"
    assert out["selected"] == ["false", "true"]
    assert out["sent"] == 0, "switching a tab talked to the agent"


def test_a_modal_opens_its_content_from_its_trigger(render_page):
    """The catalog's Modal is a TRIGGER plus CONTENT, so it is a `<dialog>` and not a card that
    happens to be hidden — which is what gives the focus trap and the Escape key instead of
    hand-rolling both. The trigger is on the page; the content is only shown once opened."""
    out = drive(render_page, f"""
        window.__store.apply({{createSurface: {{surfaceId: "modal", catalogId: "{BASIC}",
            components: [
                {{id: "root", component: "Modal", trigger: "open", content: "body"}},
                {{id: "open", component: "Text", text: "Details"}},
                {{id: "body", component: "Text", text: "the whole story"}},
            ]}}}});
        window.__mount("modal");
        const dialog = window.__host.querySelector('dialog.a2-dialog');
        const closed = dialog.open;
        window.__host.querySelector('.a2-modal-trigger').click();
        return {{closed, opened: dialog.open, trigger: window.__host.querySelector(
            '.a2-modal-trigger').textContent, body: dialog.textContent.includes('whole story')}};
    """)
    assert out["closed"] is False, "the dialog was open before its trigger was used"
    assert out["opened"] is True, "the trigger did not open the dialog"
    assert out["trigger"] == "Details"
    assert out["body"] is True
