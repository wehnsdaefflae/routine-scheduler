"""Settings → Machines card: adding a machine persists to config.yaml and renders a row; the
routine page binds a catalog machine, writing routine.yaml `machines:`. No SSH round-trip — the
scan/test buttons hit the network, which the stub harness does not provide."""

import yaml
from playwright.sync_api import expect

from rsched.config import MachineConfig


def test_machines_card_add(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    ui_page.wait_for_selector("#sec-machines", timeout=10_000)
    expect(ui_page.locator("[data-mach-empty]")).to_contain_text("no machines yet")

    # fill the add form and save
    ui_page.get_by_placeholder("name (gpu-box)").fill("gpu-box")
    ui_page.get_by_placeholder("host / IP").fill("10.0.0.9")
    ui_page.get_by_placeholder("ssh user").fill("rsched")
    ui_page.get_by_placeholder("KEY_VAR (Secrets)").fill("GPUBOX_SSH_KEY")
    ui_page.get_by_placeholder("share to mount, e.g. /srv/shared (optional)").fill("/srv/shared")
    ui_page.get_by_role("button", name="save machine").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("gpu-box saved")

    # the row appears (with the share), and the config.yaml carries the machine
    expect(ui_page.locator('[data-mach="gpu-box"]')).to_contain_text("rsched@10.0.0.9")
    expect(ui_page.locator('[data-mach="gpu-box"]')).to_contain_text("mnt/gpu-box/")
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))
    assert raw["machines"]["gpu-box"]["host"] == "10.0.0.9"
    assert raw["machines"]["gpu-box"]["key_var"] == "GPUBOX_SSH_KEY"
    assert raw["machines"]["gpu-box"]["share"] == "/srv/shared"


def test_routine_machine_binding(ui, ui_page):
    """Binding a catalog machine on the routine page writes routine.yaml `machines:`."""
    mac = MachineConfig(host="10.0.0.9", user="rsched", description="RTX 4090", tags=["gpu"])
    mac.name = "gpu-box"
    ui.server_cfg.machines = {"gpu-box": mac}   # the live server the API reads

    ui_page.goto(f"{ui.url}/#/routine/uir")
    # the machine's checkbox is inside its label row
    row = ui_page.locator("label", has_text="gpu-box")
    row.wait_for(timeout=10_000)
    row.locator("input[type=checkbox]").check()
    ui_page.get_by_role("button", name="save machines").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("machines saved")

    raw = yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["machines"] == ["gpu-box"]
