"""The Decisions page's `approve & apply`: the patch lands on the surface the engine resolved,
not on the one the page infers from whoever asked.

R1488/R1489: the home used to be derived from the ASKER's kind alone ("routines" unless the
asker was a conversation), so a proposal about a DOMAIN's shared block — which the daemon has
always been able to apply via `PATCH /api/domains/{id}` — had nowhere to say so, and every
domain-level finding ended as prose telling the operator to go and click it themselves. The
engine now resolves target AND home together at ask time and the record carries `config_home`.
"""

from __future__ import annotations

from playwright.sync_api import expect

from rsched import domains
from rsched.paths import read_yaml

from .conftest import until


def test_domain_targeted_patch_applies_to_the_domain(ui, ui_page):
    """The button PATCHes /api/domains/{id} and the shared block actually changes."""
    rec = domains.create(ui.routines, name="FAU", config={"budgets": {"max_turns": 99}})
    ui.seed_question("uir", "q-20260914-070000-1", "Narrow the FAU domain's shared budget?",
                     extra={"config_patch": {"config": {"budgets": {"max_turns": 50}}},
                            "config_target": rec["id"], "config_home": "domains"})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    # the copy names the domain it would change, and calls it a domain — an apply button that
    # does not say where it lands is the dead-button failure this bridge exists to end
    expect(card).to_contain_text(rec["id"])
    expect(card).to_contain_text("domain's behalf")
    card.get_by_role("button", name="approve & apply").click()
    expect(card).to_contain_text("applied")
    until(lambda: (domains.get(ui.routines, rec["id"]) or {}).get("config")
          == {"budgets": {"max_turns": 50}}, what="the domain patch")
    saved = domains.get(ui.routines, rec["id"])
    assert saved is not None and saved["config"] == {"budgets": {"max_turns": 50}}


def test_routine_targeted_patch_still_applies_to_the_routine(ui, ui_page):
    """The routine path (D123/F458) is unchanged by the domain one — a record with no
    `config_home` still reaches /api/routines/{slug}, which every existing config_patch
    decision relies on."""
    ui.seed_question("uir", "q-20260914-070000-2", "Raise the turn budget to 120?",
                     extra={"config_patch": {"budgets": {"max_turns": 120}}})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    card.get_by_role("button", name="approve & apply").click()
    expect(card).to_contain_text("applied")
    until(lambda: read_yaml(ui.routines / "uir" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 120, what="the routine patch")
    raw = read_yaml(ui.routines / "uir" / "routine.yaml")
    assert raw["budgets"]["max_turns"] == 120
