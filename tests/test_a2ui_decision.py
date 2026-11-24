"""A decision composed as an A2UI surface and served — D310 step 4's server side: the envelope is
a valid v1.0 `createSurface` whose every component resolves in a catalog the renderer registers,
the element types are the RICHER ones the operator asked for rather than the row of buttons the
console already draws, the shape follows the record (no options means no picker, no default means
no modal), the answer's submit is an agent EVENT carrying resolved context rather than a bespoke
POST, and the route 404s a qid no record carries — exactly as every other decision surface does.
"""

from __future__ import annotations

import pytest

from conftest import authed_client, make_test_server
from rsched.paths import atomic_write_json
from rsched.web import a2ui_decision


@pytest.fixture
def client(tmp_path, make_routine):
    """A signed-in API client over an instance with one routine, so a pending record seeded under
    it is found by the same catalog walk the Decisions page uses."""
    make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


#: One ordinary decision: a question, options, a stated default, an asking routine.
FULL = {"qid": "q-20261124-140002-7", "question": "Which route for the renderer?",
        "options": ["a — hand-written", "b — upstream SDK"], "default": "a — hand-written",
        "routine": "scheduler-builder"}


def _by_id(surface: dict) -> dict[str, dict]:
    return {c["id"]: c for c in surface["createSurface"]["components"]}


def test_the_envelope_is_one_create_surface_message_the_store_accepts():
    """It is served as a MESSAGE and not as a bespoke view, so the console's own surface and an
    agent's streamed one are indistinguishable to the renderer: exactly one envelope key, a
    surfaceId, a declared catalog, and a component with id `root` (the child of the implicit
    Surface container)."""
    surface = a2ui_decision.decision_surface(FULL)
    assert set(surface) == {"version", "createSurface"}
    body = surface["createSurface"]
    assert body["surfaceId"] == f"decision:{FULL['qid']}"
    assert body["catalogId"] == a2ui_decision.BASIC_CATALOG_ID
    assert "root" in _by_id(surface), "no component with id `root` — nothing would mount"


def test_every_component_is_a_basic_catalog_type():
    """A component the renderer's registry does not carry draws an error node in its place, so a
    composer naming a type that is not in the catalog produces a surface that renders as a column
    of failures — and the only way to know is to check against the catalog itself."""
    declared = {"Text", "Image", "Icon", "Video", "AudioPlayer", "Row", "Column", "List", "Card",
                "Tabs", "Modal", "Divider", "Button", "TextField", "CheckBox", "ChoicePicker",
                "Slider", "DateTimeInput"}
    used = {c["component"] for c in a2ui_decision.decision_surface(FULL)["createSurface"]["components"]}
    assert used <= declared, f"components no catalog declares: {sorted(used - declared)}"


def test_the_options_are_a_filterable_chip_picker_not_a_row_of_buttons():
    """THE CLAIM THE OPERATOR'S ANSWER RESTS ON — "We shouldn't be fixated on the controls we
    already use. We need to improve to UI/UX with new elements!" The console's card draws one
    button per option, which stops being usable around six and cannot be narrowed; this is a
    mutually-exclusive chip picker, bound to the answer being composed."""
    pick = _by_id(a2ui_decision.decision_surface(FULL))["pick"]
    assert pick["component"] == "ChoicePicker"
    assert pick["displayStyle"] == "chips"
    assert pick["variant"] == "mutuallyExclusive"
    assert pick["value"] == {"@path": "/picked"}
    assert [o["value"] for o in pick["options"]] == FULL["options"]


def test_a_long_option_list_offers_a_filter_and_a_short_one_does_not():
    """Filterable is a property of the DECISION, not a constant: a filter over four options is one
    more control between the reader and a choice he can see whole, and a filter over twenty is the
    difference between usable and not."""
    many = dict(FULL, options=[f"option {n}" for n in range(a2ui_decision.FILTER_FROM + 2)])
    few = dict(FULL, options=["a", "b"])
    assert _by_id(a2ui_decision.decision_surface(many))["pick"]["filterable"] is True
    assert _by_id(a2ui_decision.decision_surface(few))["pick"]["filterable"] is False


def test_the_without_an_answer_sentence_is_a_modal_the_console_has_no_equivalent_for():
    """What happens without an answer is material while deciding whether to answer now, and
    clutter the rest of the time — which is what a modal is for. The console draws that sentence
    always or never, having no modal at all."""
    components = _by_id(a2ui_decision.decision_surface(FULL))
    modal = components["without"]
    assert modal["component"] == "Modal"
    assert modal["trigger"] == "without_trigger" and modal["content"] == "without_body"
    # The default's TEXT is bound, not interpolated into the component: the data model carries
    # the decision's facts, so an updateDataModel could revise it without resending the tree.
    assert components["without_text"]["text"] == {"@path": "/default"}


def test_the_shape_follows_the_record_rather_than_a_fixed_template():
    """One composer serves every ask the instance makes, so the surface has to degrade with the
    record: a decision with no options composes without a picker (and then the typed answer is
    REQUIRED, or the surface would offer no way to answer at all), and one with no default
    composes without the modal."""
    bare = a2ui_decision.decision_surface({"qid": "q-2", "question": "Say anything"})
    components = _by_id(bare)
    assert "pick" not in components, "a decision with no options drew a picker"
    assert "without" not in components, "a decision with no default drew the modal"
    assert components["text"]["@checks"], "with nothing to pick, the typed answer must be required"
    assert components["body"]["children"] == ["heading", "ask", "text", "controls"]
    # And with options the typed field is NOT required: a chip alone is a complete answer, and
    # demanding prose beside it would make the picker decorative.
    assert "@checks" not in _by_id(a2ui_decision.decision_surface(FULL))["text"]


def test_the_submit_is_an_agent_event_carrying_the_answer_as_context():
    """The answer travels the PROTOCOL's channel: an agent event whose context names the picked
    option and the typed text as bindings, which the renderer resolves to values. A bespoke POST
    body composed in the surface would be a second answer shape, and the inbox has exactly one
    writer."""
    send = _by_id(a2ui_decision.decision_surface(FULL))["send"]
    assert send["component"] == "Button"
    event = send["action"]["event"]
    assert event["name"] == "answer_decision"
    assert event["context"] == {"qid": {"@path": "/qid"},
                                "picked": {"@path": "/picked"},
                                "text": {"@path": "/text"}}


def test_the_data_model_carries_the_answer_in_progress_as_empty_rather_than_absent():
    """Two-way binding writes `/picked` and `/text` as the user works. They are present and empty
    so the first resolution of a bound input reads "" instead of undefined — otherwise every
    input is drawn as a loading state for one frame, which the spec's progressive-rendering note
    tells renderers to expect and a composer need not cause."""
    model = a2ui_decision.decision_surface(FULL)["createSurface"]["dataModel"]
    assert model["picked"] == [] and model["text"] == ""
    assert model["question"] == FULL["question"]
    assert model["routine"] == FULL["routine"]


def test_a_record_with_nothing_in_it_still_composes_a_mountable_surface():
    """A composer that raised on a thin record would make one malformed question break the page
    it is drawn on. Absent fields are empty strings, not None: a `DynamicString` bound to null
    renders as "" anyway, and the surface stays mountable."""
    surface = a2ui_decision.decision_surface({})
    assert surface["createSurface"]["surfaceId"] == "decision:"
    assert "root" in _by_id(surface)
    assert surface["createSurface"]["dataModel"]["question"] == ""


def test_the_route_serves_a_real_pending_record_and_404s_an_unknown_qid(client):
    """The route reads the record every other decision surface reads (`decisions_read.find_
    question`), so this seeds a REAL pending file rather than stubbing the lookup: a qid that
    resolves on the Decisions page resolves here, and one that does not — an audit decision, which
    is not a question record at all — is a 404 here as well."""
    c, tmp = client
    atomic_write_json(
        tmp / "routines" / "apir" / "questions" / "pending" / "q-a2.json",
        {"qid": "q-a2", "question": "Which route for the renderer?",
         "options": ["a — hand-written", "b — upstream SDK"], "default": "a — hand-written",
         "asked": "20260707", "mode": "deferred"})
    got = c.get("/api/questions/q-a2/a2ui")
    assert got.status_code == 200, got.text
    body = got.json()["createSurface"]
    assert body["surfaceId"] == "decision:q-a2"
    assert body["dataModel"]["question"] == "Which route for the renderer?"
    # The record's own routine reaches the heading's binding, so the surface says who is asking.
    assert body["dataModel"]["routine"] == "apir"
    assert {c2["id"] for c2 in body["components"]} >= {"root", "pick", "text", "send", "without"}
    assert c.get("/api/questions/audit:F999/a2ui").status_code == 404
