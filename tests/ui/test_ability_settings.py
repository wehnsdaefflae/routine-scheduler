"""The three capability SETTINGS no conduct doc switches on get dials of their own
(components/abilities.js): how far back a run reads earlier runs (`runs`), the
consequence-reminder layer (`reminders`) and who approves a reminder written to the library
(`remind_confirm`, which only matters at `reminders: global` and is revealed there).

The panel carried all three in its state and saved them on every save, but showed a control
for none of them — so a value could only be changed by hand in routine.yaml, and a panel save
wrote back whatever it had been handed without the reader ever seeing it. The operator's
decision: three dials in "Permissions & capabilities", in the same style as the util and rule
approval dials.

One panel serves three hosts — the routine page (a draft, saved by the page's one accept), a
conversation's header (its own save) and the composer (read at create) — so the dials are
pinned on each. The disk is the proof throughout, and an untouched panel must save exactly
what it was given.
"""

from __future__ import annotations

import re

import yaml
from playwright.sync_api import expect

from rsched.grants import CONFIRM_LEVELS, RUN_HISTORY_LEVELS, SETTING_DEFAULTS
from rsched.reminders import LEVELS as REMINDER_LEVELS

from .conftest import until
from .helpers import configure, start_conversation

SETTINGS = tuple(SETTING_DEFAULTS)            # confirm, rule_confirm, remind_confirm, runs, reminders


def _stored(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _open_abilities(page) -> None:
    """The Abilities group starts folded (views/routine-config.js); open it."""
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup')) "
                  "if (d.dataset.group === 'Abilities') d.open = true; }")


def _dial(scope, key):
    return scope.locator(f'select[data-setting="{key}"]')


def _routine_dial(page, key):
    return _dial(page.locator("#sec-permissions + .panel"), key)


# ---- the routine page: a draft, saved by the one accept ------------------------------------

def test_a_routine_with_no_capabilities_block_reads_its_defaults(ui, ui_page):
    """The fixture routine names no `capabilities:` — it holds config.base.DEFAULT_CAPABILITIES,
    so its runs read the last run and its local reminder store is on. The dials must say THAT,
    not the all-off value the panel falls back to when a key is missing — and the reminder
    approval, which matters only at `global`, is not offered at all."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_abilities(ui_page)
    expect(_routine_dial(ui_page, "runs")).to_have_value("last")
    expect(_routine_dial(ui_page, "reminders")).to_have_value("local")
    expect(_routine_dial(ui_page, "remind_confirm")).to_be_hidden()
    expect(ui_page.locator(".accept-bar")).to_be_hidden()     # building is not editing
    # a dial says what it decides to a reader who cannot see the row it sits in
    expect(ui_page.get_by_role("combobox", name="how far back a run reads its earlier runs")) \
        .to_have_value("last")


def test_the_dials_show_what_the_routine_holds(ui, ui_page):
    configure(ui, capabilities={"actions": ["write_util", "revise_util"], "utils": [],
                                "confirm": "creations", "runs": "all", "reminders": "global",
                                "remind_confirm": "creations"})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_abilities(ui_page)
    expect(_routine_dial(ui_page, "runs")).to_have_value("all")
    expect(_routine_dial(ui_page, "reminders")).to_have_value("global")
    expect(_routine_dial(ui_page, "remind_confirm")).to_be_visible()
    expect(_routine_dial(ui_page, "remind_confirm")).to_have_value("creations")
    # each option carries its one line of help, the way the approval dials' options do
    expect(_routine_dial(ui_page, "runs").locator("option:checked")).to_contain_text("all —")


def test_a_dial_change_rides_the_one_accept(ui, ui_page):
    """A change is a DRAFT like every other control's: marked on the field, counted by the
    accept bar, written by its one button — and only the field the dials belong to moves."""
    path = ui.routine_dir("uir") / "routine.yaml"
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_abilities(ui_page)
    _routine_dial(ui_page, "runs").select_option("all")
    _routine_dial(ui_page, "reminders").select_option("global")
    remind = _routine_dial(ui_page, "remind_confirm")
    expect(remind).to_be_visible()
    expect(remind).to_have_value("always")                    # what the routine holds
    remind.select_option("never")
    expect(ui_page.locator('.sf-field[data-field="permissions capabilities"]')) \
        .to_have_class(re.compile(r"\bsf-changed\b"))
    assert "capabilities" not in _stored(path)                # nothing written yet

    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/settings")) as sent:
        ui_page.locator(".accept-bar [data-accept]").click()
    changes = sent.value.post_data_json["changes"]
    assert set(changes) == {"capabilities"}, changes
    assert {k: changes["capabilities"][k] for k in ("runs", "reminders", "remind_confirm")} \
        == {"runs": "all", "reminders": "global", "remind_confirm": "never"}
    until(lambda: (_stored(path).get("capabilities") or {}).get("remind_confirm") == "never",
          what="the accepted dials")
    caps = _stored(path)["capabilities"]
    assert (caps["runs"], caps["reminders"]) == ("all", "global")
    assert caps["confirm"] == "creations"                     # the dial nobody touched kept its value
    # the panel is rebuilt from the accepted read, and says what was saved
    expect(_routine_dial(ui_page, "runs")).to_have_value("all")
    expect(_routine_dial(ui_page, "remind_confirm")).to_have_value("never")


def test_the_reminder_approval_is_revealed_only_at_global(ui, ui_page):
    """`remind_confirm` decides who approves a write to the LIBRARY's reminders, which only a
    routine at `global` can make: below that it is a dial with nothing to govern. Hiding it
    changes nothing it holds — stepping back down to local and up again finds it as left."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_abilities(ui_page)
    reminders, remind = _routine_dial(ui_page, "reminders"), _routine_dial(ui_page, "remind_confirm")
    expect(remind).to_be_hidden()
    # The reveal is not a rebuild: the select changed is the select still there, and it keeps the
    # keyboard. Rebuilding the panel on a change (what a doc's own dial does) would take the
    # focus out from under someone stepping through the levels with the arrow keys.
    reminders.focus()
    reminders.evaluate("(s) => { s.dataset.probe = 'the same node'; }")
    reminders.select_option("global")
    expect(remind).to_be_visible()
    assert reminders.get_attribute("data-probe") == "the same node"
    assert ui_page.evaluate("() => document.activeElement?.dataset?.setting") == "reminders"

    remind.select_option("creations")
    reminders.select_option("local")
    expect(remind).to_be_hidden()
    reminders.select_option("global")
    expect(remind).to_have_value("creations")


def test_changing_a_dial_back_leaves_nothing_to_accept(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_abilities(ui_page)
    _routine_dial(ui_page, "runs").select_option("all")
    expect(ui_page.locator(".accept-bar")).to_be_visible()
    _routine_dial(ui_page, "runs").select_option("last")
    expect(ui_page.locator(".accept-bar")).to_be_hidden()


# ---- a conversation's header: the panel's own save -----------------------------------------

def test_an_untouched_conversation_panel_saves_what_it_loaded(ui, ui_page):
    """The header's save sends the WHOLE mapping, settings included — so a dial resting on a
    value the conversation does not hold would rewrite it on a save nobody meant as a change.
    Untouched, the save must send back exactly the settings the conversation's file holds."""
    slug, _conv_dir = start_conversation(ui, ui_page, "look into this")
    path = ui.conversations / slug / "routine.yaml"
    before = _stored(path)["capabilities"]
    ui_page.locator(".conv-caps > summary").click()
    panel = ui_page.locator(".conv-caps")
    expect(_dial(panel, "runs")).to_have_value(before["runs"])
    expect(_dial(panel, "reminders")).to_have_value(before["reminders"])

    with ui_page.expect_request(lambda r: r.method == "PUT"
                                and r.url.endswith(f"/api/conversations/{slug}/permissions")) as sent:
        ui_page.get_by_role("button", name="save permissions").click()
    body = sent.value.post_data_json["capabilities"]
    assert {k: body[k] for k in SETTINGS} == {k: before[k] for k in SETTINGS}
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("permissions saved")
    after = _stored(path)["capabilities"]
    assert {k: after[k] for k in SETTINGS} == {k: before[k] for k in SETTINGS}


def test_a_conversation_dial_change_is_saved(ui, ui_page):
    slug, _conv_dir = start_conversation(ui, ui_page, "look into this")
    path = ui.conversations / slug / "routine.yaml"
    ui_page.locator(".conv-caps > summary").click()
    panel = ui_page.locator(".conv-caps")
    _dial(panel, "runs").select_option("all")
    expect(_dial(panel, "remind_confirm")).to_be_hidden()
    _dial(panel, "reminders").select_option("global")
    _dial(panel, "remind_confirm").select_option("creations")
    ui_page.get_by_role("button", name="save permissions").click()
    until(lambda: _stored(path)["capabilities"].get("remind_confirm") == "creations",
          what="the saved conversation dials")
    caps = _stored(path)["capabilities"]
    assert (caps["runs"], caps["reminders"]) == ("all", "global")


# ---- the composer: read once, at create ----------------------------------------------------

def test_the_composer_dials_govern_the_first_reply(ui, ui_page):
    """Reply #1 fires on create, so the composer's dials are read then — a later change from
    the header would miss it."""
    ui_page.goto(f"{ui.url}/#/conversations")
    composer = ui_page.locator(".conv-setup")
    expect(_dial(composer, "reminders")).to_be_visible()
    _dial(composer, "reminders").select_option("local")
    _dial(composer, "runs").select_option("all")
    ui_page.locator(".conv-new textarea").fill("read the earlier runs")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    caps = _stored(ui.conversations / slug / "routine.yaml")["capabilities"]
    assert (caps["runs"], caps["reminders"]) == ("all", "local")


# ---- one vocabulary: the dials speak the server's levels -----------------------------------

def test_the_dials_offer_exactly_the_values_the_server_accepts(ui, ui_page):
    """A dial whose option the server refuses is a save that 422s; a server value with no
    option is a dial that cannot show what the routine holds. So each ladder is the server's
    own (grants.py, reminders.LEVELS), and a value missing from a mapping falls back to what a
    save without it writes (grants.SETTING_DEFAULTS) — the dial then shows what the save would
    hold."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    data = ui_page.evaluate("""async () => {
      const m = await import('/static/components/abilities-data.js');
      const values = (opts) => opts.map(([v]) => v);
      return { confirm: values(m.CONFIRM_OPTIONS), rule_confirm: values(m.RULE_CONFIRM_OPTIONS),
               remind_confirm: values(m.REMIND_CONFIRM_OPTIONS), runs: values(m.RUNS_OPTIONS),
               reminders: values(m.REMINDER_OPTIONS), defaults: m.SETTING_DEFAULTS };
    }""")
    for dial in ("confirm", "rule_confirm", "remind_confirm"):
        assert data[dial] == list(CONFIRM_LEVELS), (dial, data[dial])
    assert data["runs"] == list(RUN_HISTORY_LEVELS)
    assert data["reminders"] == list(REMINDER_LEVELS)
    assert data["defaults"] == SETTING_DEFAULTS


def test_the_digest_names_the_reminder_approval_where_its_dial_is(ui, ui_page):
    """A changed or overridden value is summarised in words (components/settings-digest.js) —
    a pattern's override note reads the pattern's capabilities through it. The reminder approval
    is named where its dial is shown, at `global`, and nowhere it governs nothing."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    lines = ui_page.evaluate("""async () => {
      const { describe } = await import('/static/components/settings-digest.js');
      const caps = (reminders) => ({ actions: [], utils: [], confirm: 'creations', runs: 'all',
                                     reminders, remind_confirm: 'never' });
      return [describe('capabilities', caps('global')), describe('capabilities', caps('local'))];
    }""")
    assert "previous runs: all" in lines[0] and "reminder approval: never" in lines[0], lines[0]
    assert "reminder approval" not in lines[1], lines[1]
