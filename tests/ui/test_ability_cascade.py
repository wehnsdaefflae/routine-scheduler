"""Unticking an ability card switches off that ability and no other (components/abilities.js)."""

from __future__ import annotations

import re

import yaml
from playwright.sync_api import expect

from .helpers import unfold

PENDING_DROP = re.compile(r"\bpending-drop\b")


def _card(page, slug):
    return page.locator(f'#sec-permissions + .panel .ability[data-ability="{slug}"]')


def test_unticking_a_doc_switches_off_that_doc_and_no_other(ui, ui_page):
    # `scheduling` requires the schedule_run action, which this mapping does not switch on
    path = ui.routine_dir("uir") / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg.update(permissions=["util-authoring", "scheduling"],
               capabilities={"actions": ["write_util", "revise_util"], "utils": [],
                             "confirm": "creations", "runs": "last", "reminders": "local"})
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    short = _card(ui_page, "scheduling")
    expect(short.locator("[data-switch-on]")).to_be_visible()     # its own remedy, in place

    _card(ui_page, "util-authoring").locator(".ability-head input").uncheck()
    expect(_card(ui_page, "util-authoring")).to_have_class(PENDING_DROP)
    expect(short.locator(".ability-head input")).to_be_checked()
    expect(short).not_to_have_class(PENDING_DROP)
    expect(ui_page.locator("#toast")).not_to_contain_text("also switched off")
