"""An action row's OUTCOME is visible at a glance, and a HELD row is the loudest thing there.

Operator, 2026-09-28: *"different util exit codes should change the color of the response in the
ui. also if tool calls are held because of a reminder, this also needs some visual sprucing up."*

These load the real transcript renderer in the real browser and assert the class the stylesheet
keys off, plus — for the two rows the request is actually about — that the computed style really
differs from an ordinary row. A test that only checked the class would pass over a stylesheet
that never shipped the rule.

The negative case is the one that earns its place: an ordinary nonzero exit must NOT be styled as
an error. `grep -q`, `test -f` and `diff` all answer with exit 1, and the `shell` action documents
a nonzero exit as frequently being the answer — colouring those red is the F506/F523 mistake, made
twice already, and a comment saying so does not prevent a third time.
"""
from playwright.sync_api import expect

# One observation payload per state the vocabulary distinguishes.
_FIXTURE = """async (rows) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'states'; document.body.append(box);
  const t = createTranscript(box, {isLive: () => true});
  let turn = 0;
  for (const payload of rows) {
    turn += 1;
    t.add({type:'assistant_action', turn, ts:'2026-09-28T00:00:00Z',
           payload:{kind:'util', name:'example', say:'state fixture'}});
    t.add({type:'observation', turn, ts:'2026-09-28T00:00:01Z', payload});
  }
  return box.querySelectorAll('.obs-collapse').length;
}"""


def test_each_observation_state_carries_its_own_class(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    rows = [
        {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok"},
        {"kind": "util", "name": "json", "exit": 2, "stderr": "usage: gu json ..."},
        {"kind": "util", "name": "remote", "exit": 124, "stderr": "timed out"},
        {"kind": "util", "name": "gone", "missing": True, "available": []},
        {"kind": "reminder_hold", "action": "util:fs-ops mv a b",
         "reminders": [{"id": "rem-1", "scope": "local", "description": "mv overwrites"}]},
        # the negative case: an ordinary nonzero from a shell command is a RESULT, not a fault
        {"kind": "shell", "exit": 1, "stdout": ""},
    ]
    assert ui_page.evaluate(_FIXTURE, rows) == len(rows)

    box = ui_page.locator("#states")
    expect(box.locator(".obs-collapse").nth(0)).to_have_class("obs-collapse obs-ok")
    expect(box.locator(".obs-collapse").nth(1)).to_have_class("obs-collapse obs-usage")
    expect(box.locator(".obs-collapse").nth(2)).to_have_class("obs-collapse obs-timeout")
    expect(box.locator(".obs-collapse").nth(3)).to_have_class("obs-collapse obs-refused")
    expect(box.locator(".obs-collapse").nth(4)).to_have_class("obs-collapse obs-held")
    # exit 1 from `shell`: no state at all — the row stays as quiet as any other result
    expect(box.locator(".obs-collapse").nth(5)).to_have_class("obs-collapse")


def test_a_held_row_is_visibly_louder_than_an_ordinary_one(ui, ui_page):
    """The request was for VISIBLE difference, so assert the rendered style, not the class.

    A held action is the only row in a transcript that is not history: it did not run, and the
    run is deciding again. If the stylesheet ever loses that rule the class assertions above
    would still pass while the operator sees nothing.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [
        {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok"},
        {"kind": "reminder_hold", "action": "util:fs-ops mv a b",
         "reminders": [{"id": "rem-1", "scope": "local", "description": "mv overwrites"}]},
    ])
    styles = ui_page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#states .obs-collapse > summary')];
      return rows.map((s) => {
        const c = getComputedStyle(s);
        return {colour: c.color, weight: c.fontWeight, edge: c.borderLeftColor,
                background: c.backgroundColor};
      });
    }""")
    ok, held = styles[0], styles[1]
    assert held["colour"] != ok["colour"], "a held row must not read like a successful one"
    assert held["edge"] != ok["edge"], "the held row's left edge must be its own colour"
    assert held["background"] != ok["background"], "the held row must be tinted"
    assert int(held["weight"]) > int(ok["weight"]), "a held row is emphasised, not merely tinted"


def test_a_usage_error_is_distinguishable_from_a_failure(ui, ui_page):
    """exit 2 means the CALL was malformed, so the repair is in the arguments and not in the
    work — 3.8% of fleet util calls, and the reason `usage` is its own state rather than a red
    row like any other."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [
        {"kind": "util", "name": "json", "exit": 2, "stderr": "usage: gu json ..."},
        {"kind": "util", "name": "pytest-run", "exit": 1, "stderr": "boom"},
    ])
    colours = ui_page.evaluate("""() => [...document.querySelectorAll(
        '#states .obs-collapse > summary')].map((s) => getComputedStyle(s).color)""")
    assert colours[0] != colours[1], "a usage error and a failure must not look identical"


def test_a_malformed_call_and_a_timeout_read_in_the_warning_colour(ui, ui_page):
    """Both rows were drawn in SUMMONS — coral, the colour the console reserves for what waits
    on a PERSON — while neither waits on anyone: the run repairs its own arguments, and a
    deadline is the run's to plan around. The operator: "use the warning colour instead".
    Asserted as RENDERED colour against the tokens themselves, in both themes."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [
        {"kind": "util", "name": "json", "exit": 2, "stderr": "usage: gu json ..."},
        {"kind": "util", "name": "remote", "exit": 124, "stderr": "timed out"},
    ])
    read = """() => {
      const token = (name) => {
        const p = document.createElement('span');
        p.style.color = `var(${name})`;
        document.body.append(p);
        const c = getComputedStyle(p).color;
        p.remove();
        return c;
      };
      const rows = [...document.querySelectorAll('#states .obs-collapse > summary')]
        .map((s) => getComputedStyle(s));
      return { rows: rows.map((c) => ({ colour: c.color, edge: c.borderLeftColor,
                                       style: c.borderLeftStyle })),
               warn: token('--warn'), summons: token('--summons') };
    }"""
    for theme in ("dark", "light"):
        ui_page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        got = ui_page.evaluate(read)
        assert got["warn"] != got["summons"], "the fixture cannot tell the two tokens apart"
        usage, timeout = got["rows"]
        for name, row in (("usage", usage), ("timeout", timeout)):
            assert row["colour"] == got["warn"], f"{theme}: the {name} row is {row['colour']}"
            assert row["edge"] == got["warn"], f"{theme}: the {name} row's edge is {row['edge']}"
        assert (usage["style"], timeout["style"]) == ("solid", "dashed"), got["rows"]


def test_a_write_that_ran_its_script_reads_as_the_scripts_verdict(ui, ui_page):
    """`then_script` (engine/thenscript.py): the change landed, so the row means what the script
    said about it — a red row for a failing check, a green one for a passing one — and the
    script's own output sits under the change's line. A change that did not land never ran its
    script, and the row says so instead of showing a result that does not exist."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate(_FIXTURE, [
        {"kind": "write_file", "path": "scripts/render.py", "bytes": 120,
         "then_script": {"kind": "script", "name": "render", "args": [], "exit": 1,
                         "stdout": "", "stderr": "Traceback: boom"}},
        {"kind": "edit_file", "path": "state/x.json", "replacements": 1, "bytes": 40,
         "then_script": {"kind": "script", "name": "check", "args": [], "exit": 0,
                         "stdout": "all 3 rows valid"}},
        {"kind": "edit_file", "path": "state/x.json", "error": "anchor not found",
         "then_script_skipped": "the edit_file did not land, so the script did not run"},
    ])
    rows = ui_page.locator("#states .obs-collapse")
    expect(rows.nth(0)).to_have_class("obs-collapse obs-error")
    expect(rows.nth(1)).to_have_class("obs-collapse obs-ok")
    expect(rows.nth(2)).to_have_class("obs-collapse obs-error")
    texts = ui_page.evaluate(
        "() => [...document.querySelectorAll('#states .obs-collapse .obs')].map(e => e.textContent)")
    assert "then_script render → exit 1" in texts[0] and "Traceback: boom" in texts[0]
    assert "then_script check → exit 0" in texts[1] and "all 3 rows valid" in texts[1]
    assert "[then_script NOT run]" in texts[2]
