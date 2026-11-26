"""Starting a conversation from a playbook SHOWS that playbook's setup pre-filled, and applies
it only after one explicit confirmation (D313), asserted in the browser because the whole claim
is about what a person sees and clicks. The operator chose this shape over applying the setup
silently and over pre-filling only the harmless fields (2026-11-25), which is why the subject
here is a SHELL-BEARING playbook: its `permissions: [shell]` is shown like everything else, and
what protects it is the confirmation. Three things live here and nowhere else — the proposal is
readable before anything is clicked, the unconfirmed proposal applies NOTHING (the operator's
first boundary, measured against the config reply #1 boots with), and a confirmed proposal
lands exactly the ticked settings, with an unticked one left out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from playwright.sync_api import expect

from rsched import playbooks

PANEL = ".pb-setup"
CONFIRM = ".pb-setup-confirm"
ITEM = ".pb-setup-item"


def _library(ui) -> Path:
    """The fixture console's library home (the `ui` fixture copies the seeded template into
    `tmp/library` and points `libraries_home` at it)."""
    return ui.tmp / "library"


def _only_playbook(ui, workdir: Path, readdir: Path | None = None) -> Path:
    """One SHELL-BEARING playbook in a library holding no other, so the cards on the screen are
    ours and the counts below are not the seed corpus's.

    The setup names the `shell` permission doc and two general rules the seeded library really
    holds — a slug the library lacks is dropped by the server rather than proposed (the create
    path refuses an unknown rule), so a seed drift would show here as an absent row instead of
    a mysterious green.
    """
    home = _library(ui)
    pb = home / "playbooks"
    pb.mkdir(parents=True, exist_ok=True)
    for sub in pb.iterdir():
        if sub.is_dir():
            shutil.rmtree(sub)
    assert (home / "permissions" / "shell.md").is_file()           # the subject of the test
    assert (home / "rules" / "ask-policy.md").is_file()
    playbooks.write_playbook(home, "shell-job", main=(
        "---\nslug: shell-job\ntitle: Do a shell job\n"
        "when: a task is command-line work on a machine\n"
        "axis: the machine and the job\ntags:\n- shell\n"
        "setup:\n"
        "  permissions:\n  - shell\n"
        "  rules:\n  - ask-policy\n  - verify-independently\n"
        f"  workdir: '{workdir}'\n"
        f"  fs_write_roots:\n  - '{workdir}'\n"
        + (f"  fs_read_roots:\n  - '{readdir}'\n" if readdir else "")
        + "  connections: {}\n"
        "  note: shell is the permission this playbook is about\n"
        "---\n\n## Instructions\n1. prove where you are before you act\n"))
    return home


def _config(ui, slug: str) -> dict:
    """The conversation's routine.yaml — what reply #1 actually boots with.

    This is the read-back the acceptance check asks for rather than a convenience: the rules and
    the folder access a conversation holds are PRE-START (web/api_conversation_create.py says
    so in its own words — a rule bound afterwards never governs reply #1, which has already
    fired). So the effective first-reply settings ARE this file.
    """
    return yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))


def _start(ui_page) -> str:
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    return ui_page.url.rsplit("/", 1)[-1]


def test_picking_a_playbook_shows_its_setup_prefilled_and_unconfirmed(ui, ui_page):
    """Everything the playbook needs is readable, and every proposed value is TICKED — the
    pre-fill the operator chose — while the panel says plainly that nothing has been applied."""
    workdir = ui.tmp / "proj"
    _only_playbook(ui, workdir)
    ui_page.goto(f"{ui.url}/#/conversations")
    # before anything is picked there is no panel at all: an empty box is a thing to read and
    # dismiss, and the state stamp is what this waits for rather than racing the catalog fetch
    expect(ui_page.locator('[data-pb-setup-state="none"]')).to_have_count(1)
    expect(ui_page.locator(PANEL)).to_have_count(0)

    ui_page.locator('[data-pb="shell-job"]').click()
    expect(ui_page.locator(PANEL)).to_have_count(1)
    expect(ui_page.locator('[data-pb-setup-state="proposed"]')).to_have_count(1)
    # the shell permission is SHOWN, not withheld (option (c) was declined), and pre-ticked
    shell = ui_page.locator('[data-pb-setup="permissions:shell"]')
    expect(shell).to_have_count(1)
    expect(shell).to_be_checked()
    for item in ("rules:ask-policy", "rules:verify-independently",
                 f"workdir:{workdir}", f"fs_write_roots:{workdir}"):
        box = ui_page.locator(f'[data-pb-setup="{item}"]')
        expect(box).to_have_count(1)
        expect(box).to_be_checked()
    expect(ui_page.locator(ITEM)).to_have_count(5)
    expect(ui_page.locator(".pb-setup-note")).to_contain_text(
        "shell is the permission this playbook is about")
    expect(ui_page.locator(".pb-setup-state")).to_have_text(
        "nothing is applied yet — these take effect when you confirm")
    expect(ui_page.locator(CONFIRM)).to_have_count(1)
    expect(ui_page.locator(CONFIRM)).to_be_enabled()


def test_nothing_is_applied_until_the_confirmation(ui, ui_page):
    """THE OPERATOR'S FIRST BOUNDARY, measured where it matters: with the proposal shown but
    NOT confirmed, the conversation starts without the shell permission, without the proposed
    rules and without the folder access — selecting a playbook writes no grant at all. The
    playbook itself still seeds the brief, which is what selection has always done."""
    workdir = ui.tmp / "proj"
    _only_playbook(ui, workdir)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator('[data-pb="shell-job"]').click()
    expect(ui_page.locator('[data-pb-setup="permissions:shell"]')).to_be_checked()

    slug = _start(ui_page)                      # started WITHOUT clicking confirm
    cfg = _config(ui, slug)
    # the brief DID seed — `playbook: {slug, commit}` is how conversations.py records it
    assert (cfg.get("playbook") or {}).get("slug") == "shell-job", cfg
    assert "shell" not in (cfg.get("permissions") or []), cfg
    assert "shell" not in ((cfg.get("capabilities") or {}).get("actions") or []), cfg
    # the workdir is no config key of its own: it becomes write root #1, so its absence from
    # the roots is what "the project directory was not applied" actually means
    assert str(workdir) not in (cfg.get("fs_write_roots") or []), cfg
    assert str(workdir) not in (cfg.get("fs_read_roots") or []), cfg
    for rule in ("verify-independently",):      # a rule the conversation default does not hold
        assert rule not in (cfg.get("rules") or []), cfg


def test_deselecting_the_playbook_cancels_the_proposal(ui, ui_page):
    """The negative control for CANCEL: "start fresh instead" takes the panel away with the
    selection it described, so there is nothing left to confirm and nothing is applied."""
    workdir = ui.tmp / "proj"
    _only_playbook(ui, workdir)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator('[data-pb="shell-job"]').click()
    expect(ui_page.locator(CONFIRM)).to_have_count(1)
    ui_page.locator(CONFIRM).click()                      # confirmed, then cancelled
    expect(ui_page.locator('[data-pb-setup-state="confirmed"]')).to_have_count(1)

    ui_page.locator(".pb-fresh").click()
    expect(ui_page.locator('[data-pb-setup-state="none"]')).to_have_count(1)
    expect(ui_page.locator(PANEL)).to_have_count(0)

    ui_page.locator(".conv-new textarea").fill("just a plain conversation")
    slug = _start(ui_page)
    cfg = _config(ui, slug)
    assert "shell" not in (cfg.get("permissions") or []), cfg
    assert str(workdir) not in (cfg.get("fs_write_roots") or []), cfg
    assert not cfg.get("playbook"), cfg


def test_confirming_applies_the_ticked_settings_and_leaves_an_unticked_one_out(ui, ui_page):
    """The positive half AND the second negative control in one flow: after one click the shell
    permission, the confirmed rules and the write root are what reply #1 boots with — and the
    READ root unticked before confirming is absent, so an unselected field applies nothing. The
    read root is the honest subject for that half: the workdir becomes write root #1 by its own
    rule, so unticking the write root would prove nothing. The capability `shell` requires
    appears because the SERVER raised it from the held permission doc
    (resolve_permission_layers), not because the console sent it."""
    workdir, readdir = ui.tmp / "proj", ui.tmp / "refs"
    _only_playbook(ui, workdir, readdir)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator('[data-pb="shell-job"]').click()
    box = ui_page.locator(f'[data-pb-setup="fs_read_roots:{readdir}"]')
    expect(box).to_have_count(1)
    expect(box).to_be_checked()                     # proposed, like every other field …
    box.uncheck()                                   # … and refusable, which is the point
    expect(box).not_to_be_checked()

    confirm = ui_page.locator(CONFIRM)
    confirm.click()
    expect(ui_page.locator('[data-pb-setup-state="confirmed"]')).to_have_count(1)
    expect(confirm).to_be_disabled()                # one confirmation, not a repeatable apply
    expect(confirm).to_have_text("✓ setup confirmed")

    slug = _start(ui_page)
    cfg = _config(ui, slug)
    assert "shell" in (cfg.get("permissions") or []), cfg
    assert "shell" in ((cfg.get("capabilities") or {}).get("actions") or []), cfg
    assert "ask-policy" in (cfg.get("rules") or []), cfg
    assert "verify-independently" in (cfg.get("rules") or []), cfg
    # the project directory IS write root #1 (conversations.py puts it there), which is the
    # form the engine reads — there is no `workdir` key in a conversation's config
    assert (cfg.get("fs_write_roots") or [])[:1] == [str(workdir)], cfg
    # THE UNSELECTED FIELD: nothing else in this flow would ever add a read root, so its
    # absence is evidence about the untick and about nothing else
    assert str(readdir) not in (cfg.get("fs_read_roots") or []), cfg
    assert (cfg.get("connections") or {}) == {}, cfg     # no account was ever offered
