"""One open decision, composed as an A2UI surface — the step that makes the protocol real.

D310's first three steps built the renderer; this is what SENDS it something. The operator's
order was `use https://a2ui.org/ for all decision / ask elements`, corrected to
`We shouldn't be fixated on the controls we already use. We need to improve to UI/UX with new
elements!` — so this composes a decision out of the Basic Catalog's vocabulary rather than
describing the card the console already draws.

WHAT MAKES IT RICHER THAN THE CARD IT STANDS BESIDE, element by element, because that is the
whole claim being made:

- the options are a `ChoicePicker` with `displayStyle: "chips"` and `variant:
  "mutuallyExclusive"`, filterable once there are enough of them to search. The console's card
  draws one button per option, which stops being usable somewhere around six and offers no way
  to narrow them.
- the free-text answer is a `TextField` with `variant: "longText"`, carrying a `@checks` rule
  that an answer must be present — validation the renderer evaluates, which the card has no
  concept of.
- the whole thing sits in a `Card` with a heading `Text`, and what-happens-without-an-answer is
  a `Modal`: on the page today that sentence is either always visible or absent, and a modal is
  an element the console does not have at all.
- the submit is a `Button` whose action is an AGENT EVENT (`answer_decision`) carrying the
  picked option and the typed text as resolved context — so the answer travels the protocol's
  own channel instead of a bespoke POST body.

WHAT THIS MODULE DOES NOT DO. It does not answer anything: the surface's event is routed by the
console to the one answer route every other surface posts through (`api_questions.answer`), and a
second writer of the inbox's answer shape is exactly what `answerfile.write` exists to prevent.
It also holds no renderer knowledge beyond the catalog ids — it emits JSON the protocol defines.

THE DATA MODEL carries the answer being composed (`/picked`, `/text`) and the decision's own
facts (`/question`, `/default`, `/routine`). Two-way binding means the renderer writes the first
two as the user works, and the event's context reads them back out — so the composer never has to
describe how an input reaches a submit button.
"""

from __future__ import annotations

#: The Basic Catalog, as the spec publishes it. A surface declares its default here and every
#: component below resolves against it; the scheduler's own catalog rides the same surface by
#: naming itself on a component, which is the mechanism mixable catalogs exist for.
BASIC_CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"

#: Above this many options the picker offers a filter. Below it, a filter is one more control
#: between the reader and a decision he can already see whole.
FILTER_FROM = 6


def _components(question: dict) -> list[dict]:
    """The adjacency list for one decision: ids and components, in the order a reader meets them.

    Flat, as the protocol streams it — a component names its children by id rather than nesting
    them, which is what lets a validator check the tree and an update replace one node.
    """
    options = [o for o in (question.get("options") or []) if str(o).strip()]
    body: list[str] = ["heading", "ask"]
    if options:
        body.append("pick")
    body += ["text", "controls"]

    components: list[dict] = [
        {"id": "root", "component": "Card", "child": "body"},
        {"id": "body", "component": "Column", "children": body},
        {"id": "heading", "component": "Text", "variant": "heading",
         "text": {"@call": "formatString",
                  "args": {"value": "${/routine} is asking"}}},
        {"id": "ask", "component": "Text", "text": {"@path": "/question"}},
    ]

    if options:
        components.append({
            "id": "pick", "component": "ChoicePicker",
            "label": "Pick one",
            "variant": "mutuallyExclusive",
            "displayStyle": "chips",
            "filterable": len(options) >= FILTER_FROM,
            "options": [{"value": str(o), "label": str(o)} for o in options],
            "value": {"@path": "/picked"},
        })

    components += [
        # The typed answer is REQUIRED only when there is nothing to pick: with options present,
        # a chip alone is a complete answer and demanding prose would make the picker decorative.
        {"id": "text", "component": "TextField",
         "label": "In your own words" if options else "Your answer",
         "variant": "longText",
         "placeholder": "…",
         "value": {"@path": "/text"},
         **({} if options else {"@checks": [
             {"condition": {"@call": "required", "args": {"value": {"@path": "/text"}}},
              "message": "an answer is needed"}]}),
         },
        {"id": "controls", "component": "Row", "children": (
            ["send", "without"] if question.get("default") else ["send"])},
        {"id": "send", "component": "Button", "variant": "primary", "child": "send_label",
         "action": {"event": {
             "name": "answer_decision",
             "userMessage": {"@call": "formatString",
                             "args": {"value": "Answered ${/qid}"}},
             "context": {"qid": {"@path": "/qid"},
                         "picked": {"@path": "/picked"},
                         "text": {"@path": "/text"}}}}},
        {"id": "send_label", "component": "Text", "text": "Send answer"},
    ]

    if question.get("default"):
        # A MODAL, which the console has no equivalent for: what happens without an answer is
        # material when you are deciding whether to answer now, and clutter the rest of the time.
        components += [
            {"id": "without", "component": "Modal",
             "trigger": "without_trigger", "content": "without_body"},
            {"id": "without_trigger", "component": "Button", "variant": "borderless",
             "child": "without_label"},
            {"id": "without_label", "component": "Text", "text": "without an answer?"},
            {"id": "without_body", "component": "Column",
             "children": ["without_head", "without_text"]},
            {"id": "without_head", "component": "Text", "variant": "subheading",
             "text": "If you do not answer"},
            {"id": "without_text", "component": "Text", "text": {"@path": "/default"}},
        ]

    return components


def decision_surface(question: dict) -> dict:
    """The `createSurface` message for one decision record, ready to hand to a renderer.

    `question` is a row of `GET /api/questions` (or a closing ask's payload): `qid`, `question`,
    and optionally `options`, `default`, `routine`. Missing fields are absent rather than wrong:
    a decision with no options composes without a picker, and one with no default composes
    without the modal — the shape follows the record, which is what keeps this one composer able
    to serve every ask the instance makes.
    """
    qid = str(question.get("qid") or "")
    return {
        "version": "1.0",
        "createSurface": {
            "surfaceId": f"decision:{qid}",
            "catalogId": BASIC_CATALOG_ID,
            "metadata": {"qid": qid, "routine": question.get("routine") or ""},
            "dataModel": {
                "qid": qid,
                "question": str(question.get("question") or ""),
                "routine": str(question.get("routine") or "a routine"),
                "default": str(question.get("default") or ""),
                # The answer in progress. Present and empty rather than absent, so the first
                # resolution of a bound input reads "" instead of undefined and the field is not
                # drawn as a loading state for one frame.
                "picked": [],
                "text": "",
            },
            "components": _components(question),
        },
    }
