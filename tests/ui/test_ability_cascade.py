"""Unticking an ability card switches off that ability and no other (components/abilities.js).

The panel ran a "deactivation cascade" after every untick: drop each held doc the mapping no
longer covers. An untick switches no capability off, so the only docs it ever found were the
ones ALREADY short of a requirement — a library change gave one a requirement the mapping
lacks, and its own card carries the "switch on" that fixes it — and it unticked those too,
toasting "also switched off" as if the untick had done it. A doc requires actions and utils
alone (grants.normalize_capabilities), and nothing on the cards can switch a required one off,
so the cascade had nothing left to do but that.
"""

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
