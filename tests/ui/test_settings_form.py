"""The routine page's settings as ONE form: every control edits a draft, one accept saves it.

The page used to save each section on its own button, so a person changing four things pressed
four buttons and a proposal spanning four sections had nowhere to live. Now the page reads the
routine's settings as one document, a changed value is marked where it sits (and in the group
that folds it away), and a single "accept changes" at the foot of the viewport sends every
change the person kept. A value that departs from the routine's settings PATTERN is marked too —
as an override, in a different colour, because it waits on nobody.

The disk is the proof throughout: a draft that never reached `routine.yaml` is the bug class
this file exists to catch; a mark that stays after its change was accepted is the other.
"""

from __future__ import annotations

import json
import re
import time

import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .conftest import until
from .helpers import configure, stored_config, visible_toast

DRAFT = "check the changes i recommend."
# a field's two marks, asserted by name — the order a class list is built in is nobody's claim
CHANGED = re.compile(r"\bsf-changed\b")
OVERRIDE = re.compile(r"\bsf-override\b")


def _group(page, title):
    return page.locator("details.rgroup", has=page.locator(".rgroup-title", has_text=title))


def _open_group(page, title, *, more=False):
    group = _group(page, title)
    group.evaluate("(d, more) => { d.open = true; if (more) d.querySelectorAll('details.rmore')"
                   ".forEach((m) => { m.open = true; }); }", more)
    return group


def _field(page, key):
    return page.locator(f'.sf-field[data-field="{key}"]')


def _accept_bar(page):
    return page.locator(".accept-bar")


def _seed_draft(ui, changes, *, message=DRAFT, reason="", pattern=None, slug="uir"):
    d = ui.routines / ".control" / "settings-drafts"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / f"{slug}.json", {
        "created": "2026-09-29T10:00:00+00:00", "source": "creation", "message": message,
        "reason": reason, "pattern": pattern,
        "changes": {k: {"value": v, "reason": r} for k, (v, r) in changes.items()}})
    return d / f"{slug}.json"


def test_a_change_is_marked_where_it_sits_and_accepted_with_one_button(ui, ui_page):
    """Change one value: its field says so, its closed group's head says so, the accept bar
    counts it — and nothing is written until the bar's one button is pressed. After the accept
    the file holds the value and every mark is gone."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(_accept_bar(ui_page)).to_be_hidden()          # nothing pending on arrival

    limits = _open_group(ui_page, "Limits & reach", more=True)
    keep = _field(ui_page, "keep_runs").locator("input[data-keep-runs]")
    keep.fill("12")
    keep.press("Tab")                                    # a number field commits on change

    field = _field(ui_page, "keep_runs")
    expect(field).to_have_class(CHANGED)
    expect(field.locator(".sf-note-change .sf-mark")).to_have_text("changed")
    expect(field.locator(".sf-was")).to_contain_text("keeps 30 runs")
    # the group head carries it, so a closed fold still says it holds a change
    limits.evaluate("(d) => { d.open = false; }")
    expect(limits.locator(".rgroup-head [data-group-changes]")).to_have_text("1 change")
    bar = _accept_bar(ui_page)
    expect(bar).to_be_visible()
    expect(bar.locator("[data-accept-count]")).to_have_text("1 change")
    assert (stored_config(ui).get("retention") or {}).get("keep_runs") != 12  # nothing written yet

    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/settings")) as sent:
        bar.locator("[data-accept]").click()
    assert sent.value.post_data_json["changes"] == {"keep_runs": 12}
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: (stored_config(ui).get("retention") or {}).get("keep_runs") == 12,
          what="the accepted retention")
    expect(bar).to_be_hidden()
    expect(ui_page.locator(".sf-field.sf-changed")).to_have_count(0)
    expect(limits.locator("[data-group-changes]")).to_have_count(0)


def test_revert_and_discard_put_the_saved_value_back(ui, ui_page):
    """Every change can be taken back where it sits — all of them at once from the bar."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_group(ui_page, "Identity & recipe")
    hub = ui_page.locator("[data-hub-tab]")
    hub.fill("FAU")
    field = _field(ui_page, "hub_tab")
    expect(field).to_have_class(CHANGED)
    field.locator("[data-revert]").click()
    expect(ui_page.locator("[data-hub-tab]")).to_have_value("")
    expect(_accept_bar(ui_page)).to_be_hidden()

    ui_page.locator("[data-hub-tab]").fill("FAU")
    _accept_bar(ui_page).locator("[data-discard]").click()
    expect(_accept_bar(ui_page)).to_be_hidden()
    expect(ui_page.locator("[data-hub-tab]")).to_have_value("")
    assert "hub_tab" not in stored_config(ui)


def test_a_proposal_opens_the_settings_with_its_message_and_its_values(ui, ui_page):
    """Creation writes its proposal as PENDING changes: the settings open with the message,
    the proposed values filled into their controls and marked as changes with their reasons,
    and nothing applied. Discarding the proposal puts the saved values back."""
    path = _seed_draft(ui, {"keep_runs": (45, "a daily routine keeps six weeks")},
                       reason="the recipe reads mail every day")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    banner = ui_page.locator("[data-draft-banner]")
    expect(banner).to_be_visible()
    expect(banner.locator(".db-message")).to_have_text(DRAFT)
    expect(banner).to_contain_text("the recipe reads mail every day")
    expect(_accept_bar(ui_page).locator("[data-accept-count]")).to_have_text("1 change")
    expect(_group(ui_page, "Limits & reach").locator(".rgroup-head [data-group-changes]")) \
        .to_have_text("1 change")

    _open_group(ui_page, "Limits & reach", more=True)
    field = _field(ui_page, "keep_runs")
    expect(field.locator("input[data-keep-runs]")).to_have_value("45")
    expect(field).to_have_class(CHANGED)
    expect(field.locator(".sf-why")).to_have_text("a daily routine keeps six weeks")
    assert (stored_config(ui).get("retention") or {}).get("keep_runs") != 45  # proposed, not applied

    banner.locator("[data-discard-proposal]").click()
    expect(banner).to_be_hidden()
    expect(field.locator("input[data-keep-runs]")).to_have_value("30")
    expect(_accept_bar(ui_page)).to_be_hidden()
    until(lambda: not path.exists(), what="the discarded proposal")


def test_accepting_a_proposal_applies_it_and_clears_it(ui, ui_page):
    path = _seed_draft(ui, {"keep_runs": (45, "six weeks"), "tags": (["mail"], "filter by it")})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(_accept_bar(ui_page).locator("[data-accept-count]")).to_have_text("2 changes")
    _accept_bar(ui_page).locator("[data-accept]").click()
    until(lambda: stored_config(ui).get("tags") == ["mail"], what="the accepted proposal")
    assert stored_config(ui)["retention"]["keep_runs"] == 45
    expect(ui_page.locator("[data-draft-banner]")).to_be_hidden()
    until(lambda: not path.exists(), what="the proposal file to go")


def test_an_override_names_the_patterns_value_and_goes_back_in_one_click(ui, ui_page):
    """A routine FOLLOWING a pattern whose saved value departs from it: the field is marked as an
    override with the pattern's own value beside it; "use the pattern's value" puts that value
    in the draft — a change like any other, applied by the accept."""
    configure(ui, pattern="daily-operator")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    bar = ui_page.locator("[data-pattern-bar]")
    expect(bar.locator(".pb-title")).to_have_text("Daily operator")
    overrides = int(bar.locator("[data-overrides]").get_attribute("data-overrides"))
    assert overrides > 0

    limits = _open_group(ui_page, "Limits & reach")
    expect(limits.locator(".rgroup-head .sf-count.override")).to_be_visible()
    field = _field(ui_page, "budgets")
    expect(field).to_have_class(OVERRIDE)
    note = field.locator(".sf-note-override")
    expect(note).to_contain_text("differs from Daily operator")
    expect(note.locator(".sf-patval")).to_contain_text("100 turns")
    note.locator("[data-to-pattern]").click()
    # the draft now holds the pattern's value: a change, which the override line says it undoes
    expect(field).to_have_class(CHANGED)
    expect(field).to_have_class(OVERRIDE)
    expect(field.locator("input[data-budget=max_turns]")).to_have_value("100")
    expect(field.locator(".sf-back")).to_contain_text("matches the pattern once accepted")

    _accept_bar(ui_page).locator("[data-accept]").click()
    until(lambda: stored_config(ui)["budgets"]["max_turns"] == 100, what="the pattern's budgets")
    expect(field).not_to_have_class(re.compile(r"sf-(changed|override)"))
    expect(bar.locator("[data-overrides]")).to_have_attribute("data-overrides", str(overrides - 1))


def test_save_as_new_pattern_is_offered_only_for_values_no_pattern_carries(ui, ui_page):
    """"Save as new pattern" is live exactly while the routine's SAVED values match no pattern
    in the library and nothing is pending (a pattern is saved from what the routine
    holds). Saving one makes the routine follow it — and a routine that IS its pattern offers
    no copy of it."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    save = ui_page.locator("[data-save-pattern]")
    expect(save).to_be_enabled()                          # these values are no pattern yet

    _open_group(ui_page, "Identity & recipe")
    ui_page.locator("[data-hub-tab]").fill("FAU")
    expect(save).to_be_disabled()                         # pending: accept or discard first
    assert "accept or discard" in (save.get_attribute("title") or "")
    _accept_bar(ui_page).locator("[data-discard]").click()
    expect(save).to_be_enabled()

    save.click()
    form = ui_page.locator("[data-pattern-form]")
    form.locator("[data-pattern-title]").fill("Mail steward")
    form.locator("[data-pattern-summary]").fill("Reads the mailbox every morning and sorts it.")
    form.locator("[data-pattern-save]").click()
    expect(visible_toast(ui_page)).to_contain_text("Mail steward")
    until(lambda: stored_config(ui).get("pattern") == "mail-steward",
          what="the routine following it")
    doc = yaml.safe_load((ui.server_cfg.libraries_home / "patterns" / "mail-steward.yaml")
                         .read_text(encoding="utf-8"))
    assert doc["summary"] == "Reads the mailbox every morning and sorts it."
    assert doc["from_routine"] == "uir"
    expect(ui_page.locator("[data-pattern-bar] .pb-title")).to_have_text("Mail steward")
    # the routine now carries exactly this pattern's values: no second copy is offered
    expect(save).to_be_disabled()
    expect(save).to_have_attribute("title", re.compile("its own pattern"))

    # …and once a value departs from it, a pattern with the new values is worth saving again
    _open_group(ui_page, "Limits & reach", more=True)
    keep = _field(ui_page, "keep_runs").locator("input[data-keep-runs]")
    keep.fill("7")
    keep.press("Tab")
    _accept_bar(ui_page).locator("[data-accept]").click()
    until(lambda: (stored_config(ui).get("retention") or {}).get("keep_runs") == 7,
          what="the accept")
    expect(save).to_be_enabled()


def test_following_another_pattern_only_proposes_its_values(ui, ui_page):
    """"Follow another pattern" writes nothing to the routine: it lays the pattern's differing
    values over the page as a proposal. The pattern switch rides the same one accept."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.locator("[data-follow-pattern]").click()
    picker = ui_page.locator("[data-pattern-picker]")
    choice = picker.locator('.pb-choice[data-pattern="watcher"]')
    expect(choice).to_contain_text("Watcher")
    choice.locator("[data-propose]").click()

    banner = ui_page.locator("[data-draft-banner]")
    expect(banner.locator(".db-message")).to_contain_text("follow Watcher")
    expect(ui_page.locator("[data-pattern-switch]")).to_contain_text("follows Watcher")
    assert "pattern" not in stored_config(ui)            # proposed, not written

    _accept_bar(ui_page).locator("[data-accept]").click()
    until(lambda: stored_config(ui).get("pattern") == "watcher", what="the pattern switch")
    expect(ui_page.locator("[data-pattern-bar] .pb-title")).to_have_text("Watcher")
    expect(ui_page.locator("[data-draft-banner]")).to_be_hidden()
    expect(_accept_bar(ui_page)).to_be_hidden()
    # the schedule it carries is the routine's own now, so the schedule departs from nothing
    expect(_field(ui_page, "schedule")).not_to_have_class(OVERRIDE)


def test_recommend_works_in_the_background_and_lands_as_a_proposal(ui, ui_page, monkeypatch):
    """"Recommend for this routine" is one slow model call. The page says it is working and stays
    usable meanwhile; the answer arrives as a proposal the person accepts — never applied."""
    from rsched.patterns import recommend

    def slow_recommend(server, **kw):
        time.sleep(1.5)
        return {"keep_runs": {"value": 14, "reason": "it runs every other day"}}

    monkeypatch.setattr(recommend, "choose", lambda server, **kw: "")
    monkeypatch.setattr(recommend, "recommend", slow_recommend)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.locator("[data-recommend]").click()
    expect(ui_page.locator("[data-recommend-busy]")).to_be_visible()
    expect(ui_page.locator("[data-recommend]")).to_be_disabled()
    # the rest of the page does not wait for it
    _open_group(ui_page, "Identity & recipe")
    ui_page.locator("[data-hub-tab]").fill("")

    banner = ui_page.locator("[data-draft-banner]")
    expect(banner.locator(".db-message")).to_have_text(DRAFT, timeout=20_000)
    expect(ui_page.locator("[data-recommend-busy]")).to_have_count(0)
    _open_group(ui_page, "Limits & reach", more=True)
    field = _field(ui_page, "keep_runs")
    expect(field.locator("input[data-keep-runs]")).to_have_value("14")
    expect(field.locator(".sf-why")).to_have_text("it runs every other day")
    assert (stored_config(ui).get("retention") or {}).get("keep_runs") != 14
    draft = json.loads((ui.routines / ".control" / "settings-drafts" / "uir.json")
                       .read_text(encoding="utf-8"))
    assert draft["message"] == DRAFT


def test_the_pattern_leads_the_settings_and_says_when_it_is_gone(ui, ui_page):
    """No pattern, a pattern, a deleted pattern: the bar says which, above every group."""
    configure(ui, pattern="gone-pattern")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    bar = ui_page.locator("[data-pattern-bar]")
    expect(bar.locator(".pb-title")).to_have_text("its pattern was deleted")
    expect(bar).to_contain_text("no longer in the library")
    # the bar sits above the groups it leads
    order = ui_page.evaluate("""() => {
      const bar = document.querySelector('[data-pattern-bar]');
      const groups = document.querySelector('[data-settings-groups]');
      return !!(bar.compareDocumentPosition(groups) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    assert order, "the pattern bar must lead the settings groups"
