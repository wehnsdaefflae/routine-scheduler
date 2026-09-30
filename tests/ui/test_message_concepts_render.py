"""A LOOK at the rendered message element — the screenshot a reader would see.

The two tests beside this one assert classes, hrefs and lossless text. None of that proves the
result is LEGIBLE: a highlighter whose key colour equals its background passes every assertion
above and renders an unreadable block. So this one opens the fold, captures it, and checks the
two things a render can fail that the source cannot:

1. the token colours actually DIFFER from the surrounding text colour (a palette that resolved
   to `inherit` for every class would look exactly like the old plain blob), and
2. the fold does not overflow its own box at phone width, where a 40-line action is widest.

It writes the capture to the gate's artifact dir so a human can look at it too, which is the
point of the exercise.
"""
import json
import os

_FIXTURE = """async (actions) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'render'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const payload of actions) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-09-30T00:00:00Z', payload});
  }
  // open every fold: a collapsed <details> renders nothing, and the point here is to LOOK
  for (const d of box.querySelectorAll('details.raw')) d.open = true;
  return box.querySelectorAll('pre.hl-json').length;
}"""

# Every token class must be PRESENT in the sample, or the colour check silently skips one: a
# first draft of this fixture carried only strings, so `.tok-num` matched nothing and the test
# reported "no such token rendered" against working code. Hence timeout_s (number) and all: true
# (literal) beside the strings and the punctuation the nesting provides.
_ACTION = {"kind": "util", "name": "websearch", "args": ["quantum", "--json"],
           "timeout_s": 300, "all": True, "model": None,
           "say": "searching for the paper", "note": "a side field rides along",
           "remind_feedback": {"id": "rem-20260930-223148-1", "label": "would_have"}}


def test_the_highlighted_fold_is_legible_and_fits_a_phone(ui, ui_page):
    ui_page.set_viewport_size({"width": 390, "height": 900})     # iPhone-ish, the narrow case
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    assert ui_page.evaluate(_FIXTURE, [_ACTION]) == 1

    fold = ui_page.locator("#render pre.hl-json").first

    # 1 · the palette RESOLVED: each token class has its own computed colour, and none of them
    #     equals the fold's background (the failure that every class-based assertion passes over)
    colours = fold.evaluate("""(pre) => {
      const bg = getComputedStyle(pre).backgroundColor;
      const of = (sel) => {
        const n = pre.querySelector(sel);
        return n ? getComputedStyle(n).color : null;
      };
      return {bg, key: of('.tok-key'), str: of('.tok-str'), num: of('.tok-num'),
              punc: of('.tok-punc'), const: of('.tok-const'),
              plain: getComputedStyle(pre).color};
    }""")
    kinds = ("key", "str", "num", "punc", "const")
    for name in kinds:
        assert colours[name], f"{name}: no such token rendered"
        assert colours[name] != colours["bg"], f"{name} is invisible against the background"
    # a key must be distinguishable from ordinary text, or the highlighting bought nothing
    assert colours["key"] != colours["plain"]

    # EVERY PAIR must differ, not just each one from the background. Checking classes only
    # against the background let `.tok-num` and `.tok-const` share one colour for a whole
    # release: `300`, `true` and `null` rendered identically, and every class-level assertion
    # passed. It was found by LOOKING at the capture, which is why this loop now exists.
    seen = {}
    for name in kinds:
        clash = seen.get(colours[name])
        assert clash is None, f"{name} and {clash} render in the same colour ({colours[name]})"
        seen[colours[name]] = name

    # 2 · it FITS: the pre scrolls internally (overflow-x: auto) rather than pushing the turn
    #     box wider than the viewport — a horizontal page scrollbar at phone width is the defect
    fits = ui_page.evaluate("""() => {
      const turn = document.querySelector('#render .turn');
      return {turn: turn.getBoundingClientRect().width,
              vw: document.documentElement.clientWidth,
              docScroll: document.documentElement.scrollWidth};
    }""")
    assert fits["turn"] <= fits["vw"] + 1, fits
    assert fits["docScroll"] <= fits["vw"] + 1, f"the page scrolls sideways on a phone: {fits}"

    # and leave the capture behind, so a person can look at what the assertions only describe
    out = os.environ.get("RSCHED_GATE_ARTIFACTS") or "/tmp"
    fold.screenshot(path=os.path.join(out, "message-concepts-fold.png"))
    print("capture:", os.path.join(out, "message-concepts-fold.png"),
          "| colours:", json.dumps(colours))
