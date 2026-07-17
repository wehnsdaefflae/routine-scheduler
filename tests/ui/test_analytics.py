"""Run-analytics surfaces in the real console: the routine page's recipe-health card
(version buckets, regression banner, one-click roll-back) and the Stats tab's per-util
execution table — driven end to end, asserting both the DOM and what landed on disk.
"""

import json
import subprocess

from playwright.sync_api import expect


def _toast(page):
    return page.locator("#toast:not([hidden])")


def _confirm_modal(page, label):
    page.locator(".modal-overlay").get_by_role("button", name=label, exact=True).click()


def _git(d, *args, date="2026-07-01T10:00:00+00:00"):
    import os
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], capture_output=True, text=True, check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date})
    head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    return head.stdout.strip()


def _stream(ui, records):
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_recipe_health_untracked_note(ui, ui_page):
    """The fixture routine has no git history — the card says so instead of pretending."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(ui_page.get_by_text("recipe versions aren't tracked")).to_be_visible()


def test_recipe_health_buckets_regression_and_rollback(ui, ui_page):
    """A versioned routine shows its version buckets; a clearly-worse newest version
    raises the regression banner; the roll-back button restores the recipe on disk."""
    d = ui.routine_dir("uir")
    _git(d, "init", "-q")
    (d / "main.md").write_text("# recipe v1\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "scaffold", date="2026-07-01T10:00:00+00:00")
    (d / "main.md").write_text("# recipe v2 (improver)\n", encoding="utf-8")
    _git(d, "add", "-A")
    v2 = _git(d, "commit", "-qm", "recipe: sharpen the scan",
              date="2026-07-10T10:00:00+00:00")
    _stream(ui, [
        # five clean legacy runs under v1 (date-mapped) …
        *[{"routine": "uir", "run_id": f"uir:2026070{i}-070000", "depth": 0,
           "status": "ok", "turns": 8, "tokens": 4000,
           "ts": f"2026-07-0{i}T07:10:00+00:00"} for i in range(2, 7)],
        # … then three stamped failures under v2 → fail rate 0 → 1 (≥ +0.4): flagged
        *[{"routine": "uir", "run_id": f"uir:2026071{i}-070000", "depth": 0,
           "status": "failed", "turns": 8, "tokens": 4000, "recipe_commit": v2,
           "asks_deferred": 1, "ts": f"2026-07-1{i}T07:10:00+00:00"} for i in (1, 2, 3)],
    ])

    ui_page.goto(f"{ui.url}/#/routine/uir")
    banner = ui_page.locator(".panel.err", has_text="possible regression")
    expect(banner).to_contain_text("recipe: sharpen the scan")
    expect(banner).to_contain_text("fail rate jumped")
    expect(ui_page.locator("tr", has_text="recipe: sharpen the scan")).to_contain_text("current")
    expect(ui_page.locator("tr", has_text="scaffold")).to_contain_text("date-mapped")

    ui_page.get_by_role("button", name="↩ roll back this change").click()
    _confirm_modal(ui_page, "roll back")
    expect(_toast(ui_page)).to_contain_text("recipe rolled back")
    assert (d / "main.md").read_text(encoding="utf-8") == "# recipe v1\n"
    # the revert is itself the new current version — the card re-rendered onto it
    expect(ui_page.locator("tr", has_text="recipe: revert to pre-")).to_contain_text("current")


def test_stats_utils_table(ui, ui_page):
    """The Stats tab answers the per-util questions: executed / ok / syntax errors /
    permission denials, first & last execution — and 'never' for an idle util."""
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    _stream(ui, [
        {"routine": "uir", "run_id": "uir:20260715-100000", "depth": 0, "status": "ok",
         "turns": 3, "tokens": 900, "ts": "2026-07-15T10:05:00+00:00",
         "utils": {"dir-tree": {"ok": 3, "usage_error": 1, "denied": 2}}},
    ])
    ui_page.goto(f"{ui.url}/#/stats")
    section = ui_page.locator(".stat-section", has=ui_page.get_by_role(
        "heading", name="Global utils"))
    expect(section).to_be_visible()
    row = section.locator("tr", has_text="dir-tree")
    cells = row.locator("td")
    expect(cells.nth(3)).to_have_text("4")            # executed = ok + errors + syntax
    expect(cells.nth(4)).to_contain_text("3 (75%)")   # ok share
    expect(cells.nth(6)).to_have_text("1")            # syntax err (exit 2)
    expect(cells.nth(7)).to_have_text("2")            # denied (permission problems)
    expect(cells.nth(10)).to_have_text("2026-07-15")  # first executed
    expect(cells.nth(11)).to_have_text("2026-07-15")  # last executed
    # a seeded util that never ran is honest about it
    never = section.locator("tr", has_text="vision")
    expect(never.first.locator("td").nth(3)).to_have_text("never")
