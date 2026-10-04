"""D118 phase 2 — a backgrounded call is VISIBLE in the conversation, and its result resolves.

Phase 1 made a read or a fetch runnable in the background: the turn comes back at once with a
*started* observation, and the REAL observation is appended at a later turn boundary. From the
transcript's side that is two rows for one call, and a reader has to be able to tell:

* the ⏳ row — the call is RUNNING. No exit code, no output, no verdict. It must not be rendered
  by the util branch, which would print "exit undefined" (the misrender the secret-gate branch
  already exists to prevent for the other callable-with-no-exit case), and it must not look like
  success or failure, because nothing is known yet;
* the row that RESOLVES it — the ordinary observation, carrying the same handle, rendered exactly
  as a synchronous one of its kind would be;
* the row nobody read — a result that landed after the last turn, or a call still running when
  the run ended. Nothing failed, and the result may be right there, which is precisely why it
  needs its own appearance: what is wrong is that nothing acted on it.

These load the REAL renderer and the REAL stylesheet in the real browser, and assert the computed
style for the two rows the feature is about, not only their class — a class assertion alone would
pass over a stylesheet that never shipped the rule (the lesson test_observation_states.py states).
"""
from playwright.sync_api import expect

_FIXTURE = """async (rows) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'bg'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const payload of rows) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-10-04T00:00:00Z',
           payload:{kind: payload.kind || 'util', name:'example', say:'background fixture'}});
    t.add({type:'observation', turn, ts:'2026-10-04T00:00:01Z', payload});
  }
  return box.querySelectorAll('.obs-collapse').length;
}"""

_STARTED = {"kind": "util", "background": True, "started": True, "handle": "bg1",
            "brief": "websearch", "note": "STARTED IN THE BACKGROUND as `bg1`"}
_LANDED = {"kind": "util", "name": "websearch", "exit": 0, "stdout": "42 hits",
           "background": True, "handle": "bg1", "started_turn": 1}
_UNREAD = {"kind": "util", "name": "websearch", "exit": 0, "stdout": "42 hits",
           "background": True, "handle": "bg2", "started_turn": 1, "unread": True}
_ABANDONED = {"kind": "util", "background": True, "handle": "bg3", "started_turn": 1,
              "abandoned": True, "llm_calls_abandoned": 1}


def test_a_running_call_and_its_result_are_two_distinguishable_rows(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    assert ui_page.evaluate(_FIXTURE, [_STARTED, _LANDED]) == 2

    box = ui_page.locator("#bg")
    expect(box.locator(".obs-collapse").nth(0)).to_have_class("obs-collapse obs-running")
    # the resolving row is an ORDINARY exit-0 observation: the background flag changes where it
    # came from, never how a finished result reads
    expect(box.locator(".obs-collapse").nth(1)).to_have_class("obs-collapse obs-ok")


def test_the_running_row_says_it_is_running_and_names_its_handle(ui, ui_page):
    """The summary line is all a reader sees before expanding, so the handle and the ⏳ belong
    in it — the handle is how the later row is matched to this one."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [_STARTED])
    summary = ui_page.locator("#bg .obs-collapse > summary").first
    expect(summary).to_contain_text("⏳")
    expect(summary).to_contain_text("bg1")
    expect(summary).to_contain_text("background")


def test_the_running_row_never_renders_an_undefined_exit(ui, ui_page):
    """The started observation has no exit code. Rendered by the util branch it would read
    "exit undefined" — the one thing this row must never say."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [_STARTED])
    text = ui_page.locator("#bg .obs-collapse").first.inner_text()
    assert "undefined" not in text, text
    assert "exit" not in text.lower(), text


def test_a_running_row_is_visibly_different_from_a_finished_one(ui, ui_page):
    """Assert the RENDERED style, not the class: if the stylesheet loses the rule the class
    assertions above still pass while the operator sees two identical rows."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [_STARTED, _LANDED])
    rows = ui_page.locator("#bg .obs-collapse > summary")
    running = rows.nth(0).evaluate(
        "e => { const s = getComputedStyle(e);"
        " return [s.borderLeftStyle, s.borderLeftColor, s.color].join('|'); }")
    finished = rows.nth(1).evaluate(
        "e => { const s = getComputedStyle(e);"
        " return [s.borderLeftStyle, s.borderLeftColor, s.color].join('|'); }")
    assert running != finished, f"both rows render identically: {running}"
    assert "dashed" in running, (
        f"a running call has NO verdict yet, which the dashed border is what says: {running}")


def test_a_result_the_run_never_read_is_marked_as_such(ui, ui_page):
    """Both losses — landed too late, and still running at the end — read as the same thing to
    a person: work that happened and nothing used."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    assert ui_page.evaluate(_FIXTURE, [_UNREAD, _ABANDONED]) == 2
    box = ui_page.locator("#bg")
    expect(box.locator(".obs-collapse").nth(0)).to_have_class("obs-collapse obs-lost")
    expect(box.locator(".obs-collapse").nth(1)).to_have_class("obs-collapse obs-lost")


def test_an_unread_result_does_not_read_as_an_ordinary_success(ui, ui_page):
    """Caught in the RENDER, not by a test (2026-10-04): with only a border colour to carry it,
    this row's summary read `result — websearch → exit 0 …` — character for character the
    ordinary success beside it — and its body showed the output with no hint that nothing had
    acted on it. A transcript is read by scanning summaries, so that row was a clean result to
    every reader. The class assertion passed the whole time, because the class was never what
    was wrong: assert the WORDS.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [_UNREAD, _LANDED])
    rows = ui_page.locator("#bg .obs-collapse")
    unread_summary = rows.nth(0).locator("summary").inner_text()
    landed_summary = rows.nth(1).locator("summary").inner_text()
    assert unread_summary != landed_summary, (
        f"the unread row is indistinguishable from the resolved one: {unread_summary!r}")
    assert "NEVER READ" in unread_summary.upper(), unread_summary
    expect(rows.nth(0)).to_contain_text("nothing acted on this result")


def test_an_abandoned_call_says_its_result_is_gone(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [_ABANDONED])
    row = ui_page.locator("#bg .obs-collapse").first
    expect(row).to_contain_text("bg3")
    expect(row).to_contain_text("lost")


def test_an_ordinary_observation_is_untouched_by_the_feature(ui, ui_page):
    """The negative case that earns its place: nothing without the background flag may acquire
    either new state, or every synchronous row in every transcript changes appearance."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [
        {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok"},
        {"kind": "shell", "exit": 1, "stdout": ""},
    ])
    box = ui_page.locator("#bg")
    expect(box.locator(".obs-collapse").nth(0)).to_have_class("obs-collapse obs-ok")
    expect(box.locator(".obs-collapse").nth(1)).to_have_class("obs-collapse")
