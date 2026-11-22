"""Settings → Machines setup wizard, a dialog one step at a time, with the network stubbed (scan,
test, password login): the password path leaves a saved machine whose key_var names the generated
secret, and the paste path shows one line and notices on its own when the key has landed."""

import json
import os
import re

import yaml
from playwright.sync_api import expect

from rsched import secrets

HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPinnedKey"


def _stub(ui, monkeypatch, *, reachable):
    monkeypatch.setattr(secrets, "secrets_path", lambda: ui.tmp / "secrets.env")
    replies = {"scan-host": {"host_key": HOST_KEY}, "test": {"ok": True}}
    monkeypatch.setattr("rsched.web.settings.machines._run_remote", lambda _s, args, _e: (
        (0, json.dumps(replies[args[0]]), "") if args[0] == "scan-host" or reachable()
        else (1, "", "Authentication failed")))
    logins = []
    monkeypatch.setattr("rsched.machine_bootstrap.run_with_password",
                        lambda *a, **kw: logins.append((a[3], a[4], kw)) or {"ok": True})
    return logins


def _shot(page, name):
    """Screenshots for a reviewer, only when RSCHED_UI_SHOTS names a directory."""
    if os.environ.get("RSCHED_UI_SHOTS"):
        page.locator("[data-mach-wizard]").screenshot(path=f"{os.environ['RSCHED_UI_SHOTS']}/{name}.png")


def _first_two_steps(ui, ui_page, mode, user):
    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    ui_page.locator("[data-mach-wizard-open]").click()
    wiz = ui_page.locator("[data-mach-wizard]")
    expect(wiz).to_contain_text("Step 1 of 4")
    wiz.get_by_placeholder("host name or IP, e.g. 192.168.0.50").fill("box.lan")
    expect(wiz.get_by_placeholder("name", exact=True)).to_have_value("box")      # the name follows the host
    _shot(ui_page, "1-machine")
    wiz.locator("[data-wiz-next]").click()

    expect(wiz).to_contain_text("Step 2 of 4")
    expect(wiz.locator('[data-wiz-step="host"]')).to_have_count(0)     # one step on screen
    wiz.locator(f'[data-wiz-mode="{mode}"]').check()
    wiz.get_by_placeholder("account name").fill(user)
    _shot(ui_page, f"2-account-{mode}")
    wiz.locator("[data-wiz-next]").click()
    expect(wiz).to_contain_text("Step 3 of 4")
    expect(wiz).to_contain_text("saved as the secret BOX_SSH_KEY")
    return wiz


def test_the_password_path_sets_up_a_machine(ui, ui_page, monkeypatch):
    logins = _stub(ui, monkeypatch, reachable=lambda: True)
    wiz = _first_two_steps(ui, ui_page, "account", "mark")
    wiz.get_by_placeholder("password").fill("pw")
    _shot(ui_page, "3-password")
    wiz.locator("[data-wiz-next]").click()
    expect(wiz).to_contain_text("Step 4 of 4")
    expect(wiz).to_contain_text("Connected as mark@box.lan")
    _shot(ui_page, "4-done")

    assert logins == [("mark", "pw", {"sudo": False})]
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))["machines"]["box"]
    assert raw["user"] == "mark" and raw["key_var"] == "BOX_SSH_KEY" and raw["host_key"] == HOST_KEY
    assert "OPENSSH PRIVATE KEY" in secrets.load_secrets()["BOX_SSH_KEY"]
    wiz.locator("[data-wiz-next]").click()                             # done: closes, row listed
    expect(ui_page.locator('[data-mach="box"]')).to_contain_text("mark@box.lan")


def test_the_paste_path_notices_when_the_key_lands(ui, ui_page, monkeypatch):
    landed = []
    _stub(ui, monkeypatch, reachable=lambda: bool(landed))
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    wiz = _first_two_steps(ui, ui_page, "agent", "agent")
    expect(wiz).to_contain_text("admin who can sudo")
    wiz.locator("[data-wiz-paste]").click()
    expect(wiz.locator("[data-wiz-command]")).to_have_value(re.compile(r"^sudo env AGENT_USER=agent "))
    _shot(ui_page, "3-paste")
    wiz.locator("[data-wiz-next]").click()                             # copy command
    landed.append(True)                                                # the operator pasted it
    expect(wiz).to_contain_text("Connected as agent@box.lan", timeout=15000)
