"""Settings → Machines setup wizard, end to end in the console with the network stubbed: host →
account → the key created and installed by password → tested, leaving a saved machine whose
key_var names the generated secret. The scan, test and password login are the three stubs."""

import json
import re

import yaml
from playwright.sync_api import expect

from rsched import secrets

HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPinnedKey"


def test_the_wizard_sets_up_a_machine_without_a_pasted_key(ui, ui_page, monkeypatch):
    monkeypatch.setattr(secrets, "secrets_path", lambda: ui.tmp / "secrets.env")
    replies = {"scan-host": {"host_key": HOST_KEY}, "test": {"ok": True}}
    monkeypatch.setattr("rsched.web.settings.machines._run_remote",
                        lambda _server, args, _env: (0, json.dumps(replies[args[0]]), ""))
    logins = []
    monkeypatch.setattr("rsched.machine_bootstrap.run_with_password",
                        lambda *a, **kw: logins.append((a[3], a[4], kw)) or {"ok": True})

    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    ui_page.locator("[data-mach-wizard-open]").click()
    wiz = ui_page.locator("[data-mach-wizard]")
    wiz.get_by_placeholder("name (gpu-box)").fill("box")
    wiz.get_by_placeholder("host / IP").fill("10.0.0.9")
    wiz.get_by_role("button", name="check host").click()

    step = wiz.locator('[data-wiz-step="account"]')
    step.locator('[data-wiz-mode="account"]').check()
    expect(step).to_contain_text("FULL rights")           # the admin-account warning shows
    step.get_by_placeholder("existing account").fill("mark")
    step.get_by_role("button", name="save & create login key").click()

    install = wiz.locator('[data-wiz-step="install"]')
    expect(install).to_contain_text("stored as the secret BOX_SSH_KEY")
    expect(install.locator("[data-wiz-command]")).to_have_value(re.compile("authorized_keys"))
    install.get_by_placeholder("password").fill("pw")
    install.get_by_role("button", name="install key").click()
    expect(wiz.locator('[data-wiz-step="done"]')).to_contain_text("mark@10.0.0.9 answers")

    assert logins == [("mark", "pw", {"sudo": False})]
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))["machines"]["box"]
    assert raw["user"] == "mark" and raw["key_var"] == "BOX_SSH_KEY" and raw["host_key"] == HOST_KEY
    assert "OPENSSH PRIVATE KEY" in secrets.load_secrets()["BOX_SSH_KEY"]
