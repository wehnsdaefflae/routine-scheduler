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

    # R474: the form teaches the two-key distinction inline — KEY_VAR names a Secret
    # holding the LOGIN key; the pinned host key is the SERVER's identity, filled by scan
    expect(ui_page.get_by_text("private SSH login key")).to_be_visible()
    expect(ui_page.get_by_text("SERVER's identity key")).to_be_visible()
    expect(ui_page.get_by_text("host key (pinned server identity)")).to_be_visible()

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


def test_conversation_machine_binding(ui, ui_page):
    """D102 (R475/R496): a CONVERSATION binds a catalog machine with the SAME shared card as a
    routine; the PATCH writes the conversation's routine.yaml `machines:` (where the engine's
    RSCHED_MACHINES injection reads it). Before this the Machines surface existed only on
    routine pages, so heavy conversation work had no way onto the GPU box."""
    mac = MachineConfig(host="10.0.0.9", user="rsched", description="RTX 4090", tags=["gpu"])
    mac.name = "gpu-box"
    ui.server_cfg.machines = {"gpu-box": mac}

    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("stabilize the videos on the gpu box")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]

    ui_page.locator(".conv-caps summary").click()   # ⚙ capabilities & budgets
    row = ui_page.locator("label", has_text="gpu-box")
    row.wait_for(timeout=10_000)
    row.locator("input[type=checkbox]").check()
    ui_page.get_by_role("button", name="save machines").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("machines saved")

    raw = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["machines"] == ["gpu-box"]
