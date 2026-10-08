"""PRODUCTION and DEVELOPMENT, kept apart in the real console (operator, 2026-10-08: "in the ui,
we need to clearly distinguish between production related information and development related
information … make sure though not to clutter the interface which is text heavy anyways").

The rail's Develop group (Changes, Stats, Library) is one iris band apart from the production
groups at every width; #/changes and #/changes/<slug> render the change read models
(readmodels/change_effects.py, model_fit.py) as chips and mono numbers; Recipe health moved there
from the routine page, which keeps exactly ONE line about development — summons-toned when its
newest change regressed. Months of history REBUILT from git reach the same pages, so they fold:
the weeks as small multiples, the newest releases and changes with one "show all", consecutive
`too few runs` changes as one row, a batch of rule revisions as a count, "≈" on what rests on
rebuilt runs, and an archived routine named but not linked.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import expect

from helpers import measured_run, write_usage_stream

from .helpers import unfold
from .test_mobile_nav import NAV, PHONE

WIDE = {"width": 1400, "height": 900}
DEV = ("changes", "stats", "library")


def _at(runs: list[dict]) -> list[dict]:
    """The same records, dated one a day up to yesterday — so "when" reads as the past."""
    start = datetime.now(UTC) - timedelta(days=len(runs) + 1)
    return [{**r, "ts": (start + timedelta(days=i)).isoformat()} for i, r in enumerate(runs)]


def seed_changes(ui, make_routine) -> None:
    """`uir`: a recipe change that cost more and delivered less (regressed), then a model
    change still collecting its runs (measuring). `uis`: held still across an engine release
    that halved its tokens (improved) — the release row."""
    make_routine(slug="uis")
    uir = ([measured_run(i, slug="uir") for i in range(1, 6)]
           + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1)
              for i in range(6, 11)]
           + [measured_run(i, slug="uir", recipe="c2a9f3e1", model="Sonnet high", tokens=90_000,
                           met=1) for i in (11, 12)])
    uis = ([measured_run(i, slug="uis") for i in range(1, 6)]
           + [measured_run(i, slug="uis", engine="0.397.0", tokens=20_000) for i in range(6, 11)])
    write_usage_stream(ui.routines, _at(uir) + _at(uis))


def seed_regression(ui) -> None:
    """`uir` alone, its newest change a regression."""
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir") for i in range(1, 6)]
        + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1)
           for i in range(6, 11)]))


def _group_of(page, nav: str) -> str:
    return page.evaluate(
        """(nav) => { const a = document.querySelector(`.topbar a[data-nav="${nav}"]`);
                     return a.closest('.nav-zone.dev') ? 'dev' : 'prod'; }""", nav)


def _bg(page, selector: str) -> str:
    return page.evaluate(f"() => getComputedStyle(document.querySelector({selector!r})).backgroundColor")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_rail_sets_development_apart_in_both_themes(ui, ui_page, theme):
    """Changes, Stats and Library ride one iris band labelled Develop; Work and Fleet stay
    plain. The band is a tint of its own on either theme's rail, and the page you are on inside
    it is marked in iris, not the signal the production destinations use."""
    ui_page.add_init_script(f"localStorage.setItem('rsched_theme', {theme!r})")
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    zone = ui_page.locator(".nav-zone.dev")
    expect(zone.locator(".nav-group")).to_have_text("Develop")
    expect(zone.locator("a[data-nav]")).to_have_count(3)
    assert [_group_of(ui_page, n) for n in DEV] == ["dev"] * 3
    for nav in ("conversations", "questions", "messages", "dashboard", "settings", "help"):
        assert _group_of(ui_page, nav) == "prod", f"{nav} rides the Develop band"

    assert _bg(ui_page, ".nav-zone.dev") not in (_bg(ui_page, ".topbar"), "rgba(0, 0, 0, 0)")
    marks = ui_page.evaluate("""() => {
      const probe = document.createElement('span'); probe.style.color = 'var(--iris)';
      document.body.append(probe); const iris = getComputedStyle(probe).color; probe.remove();
      const label = getComputedStyle(document.querySelector('.nav-zone.dev .nav-group')).color;
      const active = document.querySelector('.nav-zone.dev a.active');
      return { iris, label, nav: active?.dataset.nav,
               shadow: getComputedStyle(active).boxShadow };
    }""")
    assert marks["nav"] == "changes"
    assert marks["label"] == marks["iris"], marks
    assert marks["iris"] in marks["shadow"], marks


def test_the_band_survives_the_icon_rail_and_the_bottom_bar(ui, ui_page):
    """Below 1180px the labels go and below 860px the rail is one row of icons: the Develop
    band stays a tinted run of them — never a row of its own — and the bar still fits."""
    ui_page.set_viewport_size({"width": 1000, "height": 900})
    ui_page.goto(f"{ui.url}/#/stats")
    zone = ui_page.locator(".nav-zone.dev")
    expect(zone).to_be_visible()
    expect(zone.locator(".nav-group")).to_be_hidden()
    assert _bg(ui_page, ".nav-zone.dev") != _bg(ui_page, ".topbar")

    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/stats")
    expect(ui_page.locator(NAV).first).to_be_visible()
    rows = ui_page.evaluate(f"""() => [...document.querySelectorAll({NAV!r})]
        .map((a) => Math.round(a.getBoundingClientRect().top))""")
    assert len(set(rows)) == 1, f"the bottom bar broke into rows: {rows}"
    box = zone.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"] + 1
    assert _bg(ui_page, ".nav-zone.dev") != "rgba(0, 0, 0, 0)"
    # the page you are on is marked in the band's colour there too
    active = ui_page.locator(".nav-zone.dev a.active")
    expect(active).to_have_attribute("data-nav", "stats")


def test_the_changes_page_renders_releases_model_changes_and_routines(ui, ui_page, make_routine):
    seed_changes(ui, make_routine)
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    expect(ui_page.locator(".kicker.dev")).to_have_text("development")

    release = ui_page.locator('tr[data-release="0.397.0"]')
    expect(release.locator('[data-verdict="improved"]')).to_have_text("▲improved")
    expect(release).to_contain_text("×0.50")
    expect(release.locator("td").nth(2)).to_have_text("1")       # one routine judged

    model = ui_page.locator('tr[data-change="model:"]')
    expect(model).to_contain_text("model Opus high→Sonnet high")
    chip = model.locator('a[data-verdict="measuring"]')
    expect(chip).to_contain_text("uir")
    expect(chip).to_have_attribute("href", "#/changes/uir")

    row = ui_page.locator('table.chg-routines tr[data-routine="uir"]')
    expect(row.locator("td").nth(1)).to_have_text("2")
    expect(row.locator('[data-verdict="measuring"]')).to_be_visible()
    expect(ui_page.locator('table.chg-routines tr[data-routine="uis"]')).to_have_count(0)
    row.get_by_role("link", name="uir").click()
    expect(ui_page).to_have_url(f"{ui.url}/#/changes/uir")
    expect(ui_page.locator("#crumbs")).to_contain_text("Changes›uir")


def test_the_changes_page_says_when_nothing_is_measured(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/changes")
    expect(ui_page.locator("[data-unmeasured]")).to_contain_text(
        "measurement starts with the runs recorded from this release on")
    expect(ui_page.locator("#view h2")).to_have_count(0)


def test_a_routines_development_view(ui, ui_page, make_routine):
    """Its changes newest first, one opened onto the signals it was judged on; the model fit;
    and the Recipe health that used to sit on the production page."""
    seed_changes(ui, make_routine)
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    rows = ui_page.locator("tr.chg-row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator("[data-verdict]")).to_have_attribute("data-verdict", "measuring")
    regressed = rows.nth(1)
    expect(regressed).to_contain_text("recipe c1→c2a9f3e")
    expect(regressed).to_contain_text("5 → 5")
    expect(regressed).to_contain_text("engine 0.396.0")
    expect(regressed.locator("[data-verdict]")).to_have_attribute("data-verdict", "regressed")
    for dim, state in (("correctness", "same"), ("completeness", "worse"),
                       ("effectiveness", "worse")):
        expect(regressed.locator(f'.dim-mark[data-dim="{dim}"]')).to_have_attribute(
            "data-state", state)

    detail = ui_page.locator("tr.chg-detail").nth(1)
    expect(detail).to_be_hidden()
    regressed.locator(".chg-toggle").click()
    expect(detail).to_be_visible()
    tokens = detail.locator('tr[data-signal="tokens"]')
    expect(tokens).to_contain_text("40.0k → 90.0k")
    expect(tokens.locator(".sig-mark")).to_have_text("▲")
    expect(tokens.locator(".sig-mark")).to_have_attribute("data-tone", "worse")
    met = detail.locator('tr[data-signal="met_rate"]')
    expect(met).to_contain_text("100% → 50%")
    expect(met.locator(".sig-mark")).to_have_text("▼")
    expect(met.locator(".sig-mark")).to_have_attribute("data-tone", "worse")
    expect(detail.locator('tr[data-signal="turns"] .sig-mark')).to_have_attribute("data-tone", "same")
    # the keyboard folds it again
    regressed.locator(".chg-toggle").press("Enter")
    expect(detail).to_be_hidden()

    fit = ui_page.locator("table.fit-table")
    expect(fit.locator('tr[data-model="Opus high"]')).to_contain_text("10")
    expect(fit.locator('tr[data-model="Sonnet high"]')).to_contain_text("50%")

    expect(ui_page.locator("#sec-recipe-health")).to_have_text("Recipe health")
    expect(ui_page.get_by_text("recipe versions aren't tracked")).to_be_visible()


def test_the_routine_page_points_to_development_in_one_line(ui, ui_page, make_routine):
    """The production page keeps one quiet line — the count and the newest verdict — and no
    Recipe health of its own."""
    seed_changes(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    line = ui_page.locator(".page-head [data-dev-line]")
    expect(line).to_contain_text("2 measured changes")
    expect(line.locator('[data-verdict="measuring"]')).to_be_visible()
    expect(line).to_have_attribute("href", "#/changes/uir")
    expect(line).not_to_have_class("dev-line summons")
    unfold(ui_page)
    expect(ui_page.locator("#sec-recipe-health")).to_have_count(0)
    expect(ui_page.get_by_text("Recipe health", exact=True)).to_have_count(0)
    line.click()
    expect(ui_page.locator("tr.chg-row")).to_have_count(2)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_regression_summons_from_the_routine_page(ui, ui_page, theme):
    seed_regression(ui)
    ui_page.add_init_script(f"localStorage.setItem('rsched_theme', {theme!r})")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    line = ui_page.locator("[data-dev-line]")
    expect(line).to_contain_text("1 measured change")
    expect(line).to_have_class("dev-line summons")
    chip = line.locator('[data-verdict="regressed"]')
    expect(chip).to_have_class("chip bare v-summons")
    colours = ui_page.evaluate("""() => {
      const probe = document.createElement('span'); probe.style.color = 'var(--summons)';
      document.body.append(probe); const summons = getComputedStyle(probe).color; probe.remove();
      return { summons, chip: getComputedStyle(document.querySelector(
        '[data-dev-line] [data-verdict]')).color };
    }""")
    assert colours["chip"] == colours["summons"], colours


# ---- months of rebuilt history ---------------------------------------------------------------


def _dated(runs: list[dict], days: list[str]) -> list[dict]:
    """The same records on the given days, 07:00 UTC — for a test about WHICH week a run fell in."""
    return [{**r, "ts": f"{d}T07:00:00+00:00"} for r, d in zip(runs, days, strict=True)]


def test_the_weeks_strip_draws_gaps_rebuilt_weeks_and_releases(ui, ui_page):
    """Three weeks, the middle one without a run: every reading has a GAP there (null is not
    zero) while the run count reads 0; the first week, rebuilt from history, is shaded; each
    release first run that week is a tick; and each week's column carries its readout."""
    days = [f"2026-09-{d:02d}" for d in (7, 8, 9, 10, 11, 21, 22, 23, 24, 25)]   # W37, W39
    write_usage_stream(ui.routines, _dated(
        [measured_run(i, slug="uir", rebuilt=True) for i in range(1, 6)]
        + [measured_run(i, slug="uir", engine="0.397.0", tokens=60_000) for i in range(6, 11)],
        days))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    strip = ui_page.locator(".spark-strip")
    expect(strip).to_have_attribute("data-weeks", "3")
    expect(strip.locator("figure.spark-cell")).to_have_count(6)
    tokens = strip.locator('svg[data-spark="tokens"]')
    expect(tokens).to_have_attribute("role", "img")
    expect(tokens).to_have_attribute("aria-label", re.compile(r"newest ×\d.*1 week without a reading"))
    expect(tokens.locator("rect.spark-hit[data-gap]")).to_have_count(1)
    expect(tokens.locator('rect.spark-hit[data-week="2026-W38"]')).to_have_attribute("data-gap", "")
    expect(tokens.locator("path.spark-line")).to_have_attribute("data-segments", "2")
    expect(tokens.locator("line.spark-ref")).to_have_count(1)          # the ×1.00 "usual"
    # a run count of 0 is a reading, not a gap
    expect(strip.locator('svg[data-spark="runs"] rect[data-gap]')).to_have_count(0)
    expect(tokens.locator("rect.spark-rebuilt")).to_have_attribute("data-rebuilt-week", "2026-W37")
    expect(tokens.locator("line.spark-rel")).to_have_count(2)          # 0.396.0 and 0.397.0
    expect(tokens.locator('rect[data-week="2026-W39"] title')).to_contain_text(
        "5 runs · 1 release first run")
    expect(tokens.locator('rect[data-week="2026-W37"] title')).to_contain_text("all rebuilt")
    expect(strip.locator(".spark-key")).to_contain_text("2026-W37 → 2026-W39")

    # a routine's own strip: its cost against its own usual, lines met, interventions
    ui_page.goto(f"{ui.url}/#/changes/uir")
    expect(ui_page.locator("#sec-weeks")).to_be_visible()
    cells = ui_page.locator(".spark-strip figure.spark-cell")
    expect(cells).to_have_count(4)
    assert cells.evaluate_all("(fs) => fs.map((f) => f.dataset.series)") == [
        "tokens", "turns", "met_rate", "interventions"]
    expect(ui_page.locator('svg[data-spark="turns"] rect[data-gap]')).to_have_count(1)


def test_releases_show_the_newest_twelve_and_fold_the_rest(ui, ui_page):
    """Thirteen releases, one every two runs: twelve rows and one that reveals the thirteenth.
    Each release's windows hold others, which the row says — and only then."""
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir", engine=f"0.{300 + (i - 1) // 2}.0") for i in range(1, 29)]))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    rows = ui_page.locator("table.chg-releases tr[data-release]")
    expect(rows).to_have_count(13)
    expect(ui_page.locator("table.chg-releases tr[data-release]:visible")).to_have_count(12)
    expect(ui_page.locator('tr[data-release="0.301.0"]')).to_be_hidden()   # the oldest
    expect(rows.first).to_have_attribute("data-release", "0.313.0")
    expect(rows.first.locator(".chg-notes")).to_contain_text("releases in window")
    show = ui_page.locator('button[data-fold="releases"]')
    expect(show).to_have_text("show all 13 releases")
    show.click()
    expect(ui_page.locator('tr[data-release="0.301.0"]')).to_be_visible()
    expect(show).to_have_text("show fewer")
    show.click()
    expect(ui_page.locator('tr[data-release="0.301.0"]')).to_be_hidden()


def test_changes_faster_than_the_routine_ran_fold_into_one_row(ui, ui_page):
    """Six recipes in six runs: each change came before either side could be judged. They read
    as ONE row that opens onto them; the judged change before them stands on its own."""
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir") for i in range(1, 6)]
        + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1) for i in range(6, 11)]
        + [measured_run(i, slug="uir", recipe=f"c{i - 8}x", tokens=90_000, met=1)
           for i in range(11, 17)]))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    expect(ui_page.locator("#sec-changes")).to_have_text("Changes · 7")
    group = ui_page.locator("tr.chg-group")
    expect(group).to_have_count(1)
    expect(group).to_have_attribute("data-group", "6")
    expect(group).to_contain_text("6 changes came faster than it ran")
    expect(group).to_contain_text(re.compile(r"· \d+ \w{3}–\d+ \w{3}"))
    expect(ui_page.locator("tr.chg-row:visible")).to_have_count(1)
    judged = ui_page.locator("tr.chg-row:visible")
    expect(judged.locator("[data-verdict]")).to_have_attribute("data-verdict", "regressed")

    group.click()
    members = ui_page.locator("tr.chg-row.in-group")
    expect(members).to_have_count(6)
    expect(ui_page.locator("tr.chg-row:visible")).to_have_count(7)
    expect(members.first.locator("[data-verdict]")).to_have_attribute("data-verdict", "too few runs")
    # a member opens onto its signals like any change, and folding the group hides both
    members.first.locator(".chg-toggle").click()
    expect(ui_page.locator("tr.chg-detail.in-group:visible")).to_have_count(1)
    group.locator(".chg-toggle").press("Enter")
    expect(ui_page.locator("tr.chg-row:visible")).to_have_count(1)
    expect(ui_page.locator("tr.chg-detail:visible")).to_have_count(0)


def test_a_reading_on_rebuilt_runs_wears_the_mark(ui, ui_page):
    """Runs rebuilt from history: their change, and the model-fit group they form (its effort
    unknown), carry "≈" with what it means in the tooltip; a live run's change does not."""
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir", rebuilt=True) for i in range(1, 6)]
        + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1, rebuilt=True)
           for i in range(6, 11)]))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    mark = ui_page.locator("tr.chg-row .rebuilt-mark")
    expect(mark).to_have_text("≈")
    expect(mark).to_have_attribute(
        "title", "rebuilt from history — what could not be recovered is left out")
    fit = ui_page.locator('table.fit-table tr[data-model="Opus high"]')
    expect(fit.locator(".rebuilt-mark")).to_have_text("≈ 10")
    expect(fit.locator("td").nth(1)).to_have_text("—")                 # effort: unknown

    seed_regression(ui)                                                 # the same, recorded live
    ui_page.reload()
    expect(ui_page.locator("tr.chg-row")).to_have_count(1)
    expect(ui_page.locator(".rebuilt-mark")).to_have_count(0)


def test_an_archived_routine_is_named_and_not_linked(ui, ui_page):
    """`arc` is gone — archived — but met the same rule revision as `uir`: the fleet row names
    it, muted and unlinked, while `uir` stays the way into its own development view."""
    archive = ui.routines / ".archive" / "arc-20261020-000000"
    archive.mkdir(parents=True)
    (archive / "routine.yaml").write_text("name: arc\nslug: arc\n", encoding="utf-8")

    def runs(slug: str) -> list[dict]:
        return ([measured_run(i, slug=slug) for i in range(1, 6)]
                + [measured_run(i, slug=slug, rules={"web-research": "r2"}) for i in range(6, 11)])

    write_usage_stream(ui.routines, _at(runs("arc")) + _at(runs("uir")))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    row = ui_page.locator('tr[data-change="rule:web-research"]')
    expect(row).to_contain_text("rule web-research")
    expect(row.locator("[data-alone]")).to_have_text("alone in 2 of 2")
    gone = row.locator('[data-archived="arc-20261020-000000"]')
    expect(gone).to_have_text(re.compile(r"arc \(archived\)$"))
    assert gone.evaluate("(n) => n.tagName") == "SPAN"
    expect(row.locator('a[href="#/changes/uir"]')).to_contain_text("uir")
    expect(row.locator("a", has_text="arc")).to_have_count(0)
    expect(ui_page.locator('table.chg-routines tr[data-routine^="arc"]')).to_have_count(0)


def test_a_batch_of_rule_revisions_reads_as_a_count(ui, ui_page):
    """Eleven rules revised at one run: the change names the count and opens onto the list
    without opening its signals; on the fleet page each rule is a row of its own — none of them
    alone, so each also carries the batch's verdict — and the eleventh folds."""
    rules = [f"rule-{c}" for c in "abcdefghijk"]
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir", rules=dict.fromkeys(rules, "r1")) for i in range(1, 6)]
        + [measured_run(i, slug="uir", rules=dict.fromkeys(rules, "r2"), tokens=90_000)
           for i in range(6, 11)]))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    row = ui_page.locator("tr.chg-row")
    toggle = row.locator("button.chg-rules-toggle")
    expect(toggle).to_have_text("11 rules revised")
    expect(row.locator(".chg-rules")).to_be_hidden()
    toggle.click()
    expect(row.locator(".chg-rules")).to_be_visible()
    expect(row.locator(".chg-rules")).to_contain_text("rule rule-a")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(ui_page.locator("tr.chg-detail")).to_be_hidden()          # the signals stay folded

    ui_page.goto(f"{ui.url}/#/changes")
    fleet = ui_page.locator("table.chg-fleet tr[data-change]")
    expect(fleet).to_have_count(11)
    expect(ui_page.locator("table.chg-fleet tr[data-change]:visible")).to_have_count(10)
    first = fleet.first
    expect(first.locator("[data-alone]")).to_have_text("alone in 0 of 1")
    expect(first.locator("[data-together]")).to_contain_text("with other changes:")
    expect(first.locator("[data-together]")).to_contain_text("(1 routine)")
    ui_page.locator('button[data-fold="fleet"]').click()
    expect(ui_page.locator("table.chg-fleet tr[data-change]:visible")).to_have_count(11)
