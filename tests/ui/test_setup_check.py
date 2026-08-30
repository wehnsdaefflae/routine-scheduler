"""The setup check on the routine page — the surface, rendered where somebody will see it."""
from __future__ import annotations

import yaml


def _hold_util(ui, slug: str, name: str, *, secrets: str = "(none)", fs: str = "none") -> None:
    """Give the fixture routine a reserved util whose header declares something it lacks."""
    d = ui.server_cfg.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        f'"""{name} — t.\n\nusage: gu {name}\ncalls: (none)\ntags: t\n'
        f'secrets: {secrets}\nnet: none\nfs: {fs}\n"""\n', encoding="utf-8")
    path = ui.routines / slug / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Hold NOTHING else: writing an explicit capabilities mapping replaces the model's default
    # (write_util + the memory pair), which would orphan the default permissions and add rows
    # this test is not about. One util, one gap, one row to assert.
    cfg["permissions"] = []
    cfg["capabilities"] = {"actions": [], "utils": [name], "util_tags": []}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_a_ready_routine_shows_no_strip(ui_page, ui):
    """A panel that is always there is a panel nobody reads."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("[data-routine-hero], h1")
    ui_page.wait_for_timeout(400)
    assert ui_page.locator("[data-setup-check]").count() == 0


def test_a_blocking_gap_is_named_above_the_panels(ui_page, ui):
    """The case the whole surface exists for: a util whose private store no grant covers, which
    was previously discoverable only by the run failing."""
    _hold_util(ui, "uir", "sig", fs="rw /srv/sig-sessions")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    strip = ui_page.locator("[data-setup-check]")
    strip.wait_for(state="visible", timeout=10000)
    assert "has-blocks" in (strip.get_attribute("class") or "")
    row = ui_page.locator('.setup-row[data-entity="fs-write:/srv/sig-sessions"]')
    assert row.count() == 1
    assert "fails" in row.locator(".setup-sev").inner_text().lower()
    assert "cannot reach it" in row.inner_text()
    # and the strip leads with the count, so the page says how bad it is before saying why
    assert "will fail" in ui_page.locator(".setup-head").inner_text()


def test_a_note_renders_without_making_the_strip_look_broken(ui_page, ui):
    """The three severities have to READ apart, or the strip is a wall of equal-looking rows.
    A write root over the routine's own dir is the note case: intended for the improver,
    frequently unintended elsewhere, and nothing is failing.

    (What each severity MEANS is pinned in tests/test_surface.py — the store cannot be seeded
    from here, so this asserts what the page can actually show: the row, its class, its wording.)
    """
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["fs_write_roots"] = [str(ui.routines / "uir")]
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/routine/uir")
    strip = ui_page.locator("[data-setup-check]")
    strip.wait_for(state="visible", timeout=10000)
    assert "has-blocks" not in (strip.get_attribute("class") or "")   # a note is not a failure
    row = ui_page.locator(".setup-row.sev-note")
    assert row.count() == 1
    assert "own-recipe editing is unlocked" in row.inner_text()
