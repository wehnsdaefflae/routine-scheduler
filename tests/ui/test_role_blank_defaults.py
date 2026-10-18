"""What a BLANK model role means, said once per role and said correctly.

The defect this file pins was found by LOOKING at the shipped page (0.430.0, which added the
`supervisor` row): every role's blank option was built from one template, `— system default
(<system model>) —`, so the supervisor row claimed the system model while its own description
beside it said "blank = main" and `config.base.supervisor_role` resolved it to main. Two
statements of one default, disagreeing, side by side — and the uncensored row promised a
fallback that does not exist at all (`endpoints.for_uncensored`: an unset role means referral
OFF, there is no system_model behind it).

So the blank label is per role, taken from the code that resolves it, and the supervisor's
follows the `main` picker live — a label naming the model main had when the row was drawn is
the same defect one edit later.
"""

from playwright.sync_api import expect

from rsched.config.modelconf import ModelConfig

from .helpers import configure


def _open_models(page) -> None:
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup')) "
                  "if (d.dataset.group === 'Models') d.open = true; }")


def _blank(page, role):
    return page.locator(f'select[data-model-role="{role}"] option[value=""]')


def test_each_role_states_the_default_its_own_resolver_applies(ui, ui_page):
    """The fixture's system model is `m`; the routine names none of the four roles."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_models(ui_page)

    # main and tool_call DO fall back to the server's system model (for_model_chain)
    expect(_blank(ui_page, "main")).to_have_text("— system default (m) —")
    expect(_blank(ui_page, "tool_call")).to_have_text("— system default (m) —")
    # uncensored has no fallback whatsoever — blank means the referral is off
    expect(_blank(ui_page, "uncensored")).to_have_text("— off · a refusal is not referred —")
    # supervisor falls back to MAIN (D164), which here is itself unset and so reads onward
    expect(_blank(ui_page, "supervisor")).to_have_text("— main (m) —")

    # and the row no longer says it twice: the description carries no second default
    row = ui_page.locator('select[data-model-role="supervisor"]').locator("xpath=..")
    assert "blank = main" not in row.inner_text()


def test_the_supervisors_blank_names_the_routines_own_main(ui, ui_page):
    configure(ui, models={"main": "m"})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_models(ui_page)
    expect(_blank(ui_page, "supervisor")).to_have_text("— main (m) —")


def test_changing_main_repaints_the_supervisors_blank_before_any_accept(ui, ui_page):
    """fieldBlock does not rebuild a control on its own edit, so the label has to repaint
    itself — otherwise it keeps naming the model main had when the row was drawn."""
    first = ui.server_cfg.models["m"]
    ui.server_cfg.models["other"] = ModelConfig(name="other", endpoint=first.endpoint,
                                                model=first.model)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_models(ui_page)
    expect(_blank(ui_page, "supervisor")).to_have_text("— main (m) —")

    ui_page.locator('select[data-model-role="main"]').select_option("other")
    expect(_blank(ui_page, "supervisor")).to_have_text("— main (other) —")
    # the roles that do NOT depend on main are untouched by that edit
    expect(_blank(ui_page, "tool_call")).to_have_text("— system default (m) —")
    expect(_blank(ui_page, "uncensored")).to_have_text("— off · a refusal is not referred —")
