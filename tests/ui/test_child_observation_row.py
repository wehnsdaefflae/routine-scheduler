"""A child run's observation rows read as sentences, not as JSON dumps (D241 follow-up).

0.566.0 renamed `spawn`/`subtask` to `child` (with a `mode`) and `subruns` to `children`, but
`components/obs-text.js` kept its branches keyed on the old names. Every `child` and `children`
observation therefore fell through to the last branch, `JSON.stringify` — the row existed and
the reader got the payload instead of what happened. The rows below load the REAL renderer and
assert the rendered TEXT, including the one thing the old `spawn` branch could not say: which
MODE the child runs in.
"""
from playwright.sync_api import expect

_FIXTURE = """async (rows) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'kids'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const [action, payload] of rows) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-11-21T00:00:00Z',
           payload:{say:'child fixture', ...action}});
    t.add({type:'observation', turn, ts:'2026-11-21T00:00:01Z', payload});
  }
  return box.querySelectorAll('.obs-collapse').length;
}"""

_PARALLEL = ({"kind": "child", "prompt": "survey", "label": "survey"},
             {"kind": "child", "mode": "parallel", "n": 1, "label": "survey",
              "workflow": "general-task", "note": "", "running": 1})
_SEQUENTIAL = ({"kind": "child", "mode": "sequential", "prompt": "build", "label": "build"},
               {"kind": "child", "mode": "sequential", "n": 2, "label": "build",
                "workflow": "general-task", "note": "", "started": True})
_REJECTED = ({"kind": "child", "prompt": "x", "label": "x"},
             {"kind": "child", "mode": "parallel", "rejected": True, "label": "x",
              "reason": "the run is at its child-run cap"})
_CHILDREN = ({"kind": "children"},
             {"kind": "children", "count": 1, "rows": [
                 {"n": 1, "label": "survey", "workflow": "general-task", "state": "running",
                  "turns": 3, "elapsed_s": 12}]})


def _open(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")


def test_child_rows_say_what_started_and_in_which_mode(ui, ui_page):
    _open(ui, ui_page)
    assert ui_page.evaluate(_FIXTURE, [_PARALLEL, _SEQUENTIAL, _REJECTED]) == 3
    rows = ui_page.locator("#kids .obs-collapse")
    parallel, sequential, rejected = (rows.nth(i).inner_text() for i in range(3))
    for text in (parallel, sequential, rejected):
        assert '"kind"' not in text, f"the row is the raw payload: {text}"
    assert 'sub-workflow #1 "survey" started' in parallel and "in parallel" in parallel
    assert 'sub-workflow #2 "build" started' in sequential and "sequentially" in sequential
    assert "child REJECTED: the run is at its child-run cap" in rejected


def test_the_children_table_lists_each_child(ui, ui_page):
    _open(ui, ui_page)
    ui_page.evaluate(_FIXTURE, [_CHILDREN])
    row = ui_page.locator("#kids .obs-collapse").first
    expect(row).to_contain_text('#1 "survey" [general-task] running · 3 turns · 12s')
    assert '"kind"' not in row.inner_text()
