"""F723 — a CANCELLED background call's row, and the kill confirmation that names what it killed.

Two misrenders on one seam, both of the worst kind: loud but FALSE. The reader is not shown a
gap, they are shown a sentence that is wrong.

* `kill handle=bg1` rendered as **"sub-workflow #undefined terminated"**. The kill branch read a
  sub-workflow NUMBER (`o.n`) from an observation that carries a background HANDLE instead, so
  the one confirmation a cancelling run gets named nothing — and named it in the vocabulary of
  the other namespace, which the same action deliberately keeps separate (D176 option (a): `n`
  stays sub-workflows, `handle` addresses a background call).
* A cancelled background call's own observation row (`background.cancel` records one, rather than
  dropping it, so the record does not stop mid-sentence) fell through every background branch —
  `started`, `abandoned`, `unread` — into the ORDINARY util branch, where it rendered
  `undefined → exit undefined` and carried no state at all. The row existed and said nothing
  true: not that the call was cancelled, not which handle, and not the one distinction the engine
  works hardest to keep (`stopped_work`) — whether the WORK was really stopped or only its
  delivery.

The engine's vocabulary is used as it stands, and the three endings it distinguishes are each
pinned here, because a surface that folds them cannot tell a run that chose to drop a result from
a run that ran out of turns (`background.cancel`'s own docstring makes that the reason the payload
says `cancelled` rather than `abandoned`).

These load the REAL renderer and the REAL stylesheet in the real browser and assert the rendered
TEXT — the lesson test_background_chip.py's unread case paid for: that row's class assertion
passed the whole time it was misreading, because the class was never what was wrong.
"""
from playwright.sync_api import expect

# Action/observation PAIRS, so the action row's own brief can be asserted too: a kill that
# carried a handle rendered an empty brief (`String(a[BRIEF_FIELD.kill])` = `a.n` = undefined).
_FIXTURE = """async (rows) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'cx'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const [action, payload] of rows) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-10-19T00:00:00Z',
           payload:{say:'cancel fixture', ...action}});
    t.add({type:'observation', turn, ts:'2026-10-19T00:00:01Z', payload});
  }
  return box.querySelectorAll('.obs-collapse').length;
}"""

# `background.cancel` writes this observation event for the cancelled call itself.
_STOPPED = {"kind": "util", "background": True, "handle": "bg1", "started_turn": 3,
            "cancelled": True, "had_landed": False, "stopped_work": True,
            "note": "the run cancelled this background call; its result was not delivered"}
# an llm/decide/read: the delivery stops, the work runs to its own end unread
_DELIVERY_ONLY = {"kind": "llm", "background": True, "handle": "bg2", "started_turn": 2,
                  "cancelled": True, "had_landed": False, "stopped_work": False,
                  "usage_booked": {"total_tokens": 812}}
# a call that had already finished: the work was done, the answer was thrown away
_LANDED = {"kind": "util", "background": True, "handle": "bg3", "started_turn": 1,
           "cancelled": True, "had_landed": True, "stopped_work": False}
# and the kill action's OWN confirmation row
_KILL_HANDLE = {"kind": "kill", "handle": "bg1", "cancelled": True, "background_kind": "util",
                "brief": "websearch", "had_landed": False, "stopped_work": True,
                "in_flight": ["bg2"]}
_KILL_NUMBER = {"kind": "kill", "n": 2, "killed": True, "status": "aborted"}

_ACT_KILL_HANDLE = {"kind": "kill", "handle": "bg1"}
_ACT_KILL_NUMBER = {"kind": "kill", "n": 2}
_ACT_UTIL = {"kind": "util", "name": "example"}


def _open(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")


def test_the_kill_confirmation_names_the_handle_it_cancelled(ui, ui_page):
    """The defect verbatim: `kill handle=bg1` read "sub-workflow #undefined terminated".

    A run cancelling a background call gets exactly ONE confirmation, and it must name the thing
    it stopped. "#undefined" names nothing, and "sub-workflow" names the wrong namespace.
    """
    _open(ui, ui_page)
    assert ui_page.evaluate(_FIXTURE, [[_ACT_KILL_HANDLE, _KILL_HANDLE]]) == 1
    row = ui_page.locator("#cx .obs-collapse").first
    text = row.inner_text()
    assert "undefined" not in text, text
    assert "sub-workflow" not in text, (
        f"a background call is not a sub-workflow — the two namespaces are separate: {text}")
    expect(row.locator("summary")).to_contain_text("bg1")


def test_a_kill_by_number_still_reads_as_a_sub_workflow(ui, ui_page):
    """The negative case that earns its place: `kill n=2` is the OTHER namespace and must keep
    its own wording — a fix that renamed both would have broken the half that worked."""
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [[_ACT_KILL_NUMBER, _KILL_NUMBER]])
    summary = ui_page.locator("#cx .obs-collapse > summary").first.inner_text()
    assert "undefined" not in summary, summary
    assert "sub-workflow #2" in summary, summary


def test_the_kill_confirmation_says_whether_the_work_stopped(ui, ui_page):
    """`stopped_work` is the engine's own distinction and the reason the field exists: for a
    util/script/shell the process group is terminated, for an llm/decide/read only the delivery
    stops. A confirmation that does not say which one happened leaves the run guessing."""
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [
        [_ACT_KILL_HANDLE, _KILL_HANDLE],
        [{"kind": "kill", "handle": "bg2"},
         {"kind": "kill", "handle": "bg2", "cancelled": True, "background_kind": "llm",
          "brief": "summarise", "had_landed": False, "stopped_work": False, "in_flight": []}],
    ])
    rows = ui_page.locator("#cx .obs-collapse")
    stopped, delivery = rows.nth(0).inner_text(), rows.nth(1).inner_text()
    assert stopped != delivery, f"both cancels read identically: {stopped!r}"
    assert "stopped" in stopped.lower(), stopped
    assert "not stopped" in delivery.lower() or "delivery" in delivery.lower(), delivery


def test_a_cancelled_background_row_says_it_was_cancelled(ui, ui_page):
    """The row that fell through to the util branch: `undefined → exit undefined`, no state.

    It must state the truth a reader needs — that the call was cancelled, which handle it was —
    and it must never render an exit code it does not have.
    """
    _open(ui, ui_page)
    assert ui_page.evaluate(_FIXTURE, [[_ACT_UTIL, _STOPPED]]) == 1
    row = ui_page.locator("#cx .obs-collapse").first
    text = row.inner_text()
    assert "undefined" not in text, text
    assert "exit" not in text.lower(), (
        f"a cancelled call has no exit code, so the row must not print one: {text}")
    summary = row.locator("summary").inner_text()
    assert "cancel" in summary.lower(), summary
    assert "bg1" in summary, summary


def test_a_cancelled_row_carries_its_own_state_not_an_ordinary_one(ui, ui_page):
    """It is neither `lost` nor a quiet ordinary result: `background.cancel` records `cancelled`
    rather than `abandoned` precisely so a deliberate drop and a run that ran out of turns are
    not folded together."""
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [
        [_ACT_UTIL, _STOPPED],
        [_ACT_UTIL, {"kind": "util", "name": "websearch", "exit": 0, "stdout": "42 hits"}],
        [_ACT_UTIL, {"kind": "util", "background": True, "handle": "bg9", "started_turn": 1,
                     "abandoned": True}],
    ])
    box = ui_page.locator("#cx")
    expect(box.locator(".obs-collapse").nth(0)).to_have_class("obs-collapse obs-cancelled")
    expect(box.locator(".obs-collapse").nth(1)).to_have_class("obs-collapse obs-ok")
    expect(box.locator(".obs-collapse").nth(2)).to_have_class("obs-collapse obs-lost")


def test_a_cancelled_row_is_visibly_not_an_ordinary_result(ui, ui_page):
    """Assert the RENDERED style, not only the class: a class whose stylesheet rule never
    shipped leaves the operator looking at a row that reads like any other."""
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [
        [_ACT_UTIL, _STOPPED],
        [_ACT_UTIL, {"kind": "util", "name": "websearch", "exit": 0, "stdout": "42 hits"}],
    ])
    style = """() => [...document.querySelectorAll('#cx .obs-collapse > summary')].map((s) => {
      const c = getComputedStyle(s);
      return [c.color, c.borderLeftColor, c.borderLeftStyle, c.backgroundColor].join('|');
    })"""
    cancelled, ok = ui_page.evaluate(style)
    assert cancelled != ok, f"a cancelled row renders exactly like a successful one: {cancelled}"


def test_the_three_endings_a_cancel_can_have_each_read_differently(ui, ui_page):
    """Stopped work, delivery-only, and a result that had already landed are three different
    losses, and the payload distinguishes all three (`stopped_work`, `had_landed`)."""
    _open(ui, ui_page)
    assert ui_page.evaluate(_FIXTURE, [[_ACT_UTIL, _STOPPED],
                                       [_ACT_UTIL, _DELIVERY_ONLY],
                                       [_ACT_UTIL, _LANDED]]) == 3
    rows = ui_page.locator("#cx .obs-collapse")
    stopped, delivery, landed = (rows.nth(i).inner_text() for i in range(3))
    assert len({stopped, delivery, landed}) == 3, (
        f"two of the three endings read the same: {[stopped, delivery, landed]}")
    for text in (stopped, delivery, landed):
        assert "undefined" not in text, text
    assert "bg2" in delivery and "bg3" in landed
    assert "already finished" in landed.lower(), landed


def test_the_kill_actions_own_row_names_the_handle_in_its_brief(ui, ui_page):
    """The ACTION line, not the observation: `BRIEF_FIELD.kill` is `n` (held in lockstep with
    the Python map by tests/test_actions.py), so a handle-addressed kill rendered a BLANK brief
    — the same defect one line earlier in the same row."""
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [[_ACT_KILL_HANDLE, _KILL_HANDLE],
                                [_ACT_KILL_NUMBER, _KILL_NUMBER]])
    briefs = ui_page.locator("#cx .turn .act .muted")
    assert "undefined" not in briefs.nth(0).inner_text()
    expect(briefs.nth(0)).to_contain_text("bg1")
    expect(briefs.nth(1)).to_contain_text("2")
