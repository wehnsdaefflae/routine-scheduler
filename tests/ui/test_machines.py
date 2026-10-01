"""Settings → Machines card: adding a machine persists to config.yaml and renders a row; the
routine page binds a catalog machine, writing routine.yaml `machines:`. No SSH round-trip — the
scan/test buttons hit the network, which the stub harness does not provide."""

from pathlib import Path

import yaml
from playwright.sync_api import expect

from rsched.config import MachineConfig

from .conftest import until
from .helpers import unfold


def test_machines_card_add(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    ui_page.wait_for_selector("#sec-machines")
    expect(ui_page.locator("[data-mach-empty]")).to_contain_text("no machines yet")

    # The add form is a disclosure, like "+ add model" and "+ add endpoint" two sections up:
    # eight inputs and two teaching paragraphs standing open cost a screen on every visit to
    # Settings for something done once per box.
    ui_page.locator('[data-add="machine"] summary').click()

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
    ui_page.locator("[data-mach-exclusive]").check()     # one job at a time: a GPU box
    ui_page.get_by_role("button", name="save machine").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("gpu-box saved")

    # the row appears (with the share), and the config.yaml carries the machine
    expect(ui_page.locator('[data-mach="gpu-box"]')).to_contain_text("rsched@10.0.0.9")
    expect(ui_page.locator('[data-mach="gpu-box"]')).to_contain_text("mnt/gpu-box/")
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))
    assert raw["machines"]["gpu-box"]["host"] == "10.0.0.9"
    assert raw["machines"]["gpu-box"]["key_var"] == "GPUBOX_SSH_KEY"
    assert raw["machines"]["gpu-box"]["share"] == "/srv/shared"
    assert raw["machines"]["gpu-box"]["exclusive"] is True
    # a save clears the form for the next box, the exclusive box included
    expect(ui_page.locator("[data-mach-exclusive]")).not_to_be_checked()


def _seed_exclusive_box(ui) -> Path:
    """An exclusive GPU box in BOTH places the console reads: config.yaml (what a save rewrites)
    and the live server config (what the GET lists)."""
    cfg = ui.tmp / "config.yaml"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    raw["machines"] = {"gpu-box": {"host": "10.0.0.9", "user": "rsched", "exclusive": True}}
    cfg.write_text(yaml.safe_dump(raw), encoding="utf-8")
    mac = MachineConfig(host="10.0.0.9", user="rsched", exclusive=True)
    mac.name = "gpu-box"
    ui.server_cfg.machines = {"gpu-box": mac}
    return cfg


def test_editing_a_machine_keeps_it_exclusive(ui, ui_page):
    """`exclusive: true` is what makes a box's `remote submit` take a fair-share queue ticket
    instead of launching (docs/remote-machines.md). The form neither showed nor sent it, and the
    PUT replaces the machine's whole spec — so editing an exclusive GPU box, its description
    say, wrote it back as `exclusive: false`, and the next two jobs shared one card."""
    cfg = _seed_exclusive_box(ui)
    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    row = ui_page.locator('[data-mach="gpu-box"]')
    expect(row).to_contain_text("exclusive")             # the row says the box queues
    row.get_by_role("button", name="edit").click()
    expect(ui_page.locator("[data-mach-exclusive]")).to_be_checked()   # and the edit carries it
    ui_page.get_by_placeholder("one-line description").fill("RTX 4090")
    ui_page.get_by_role("button", name="save machine").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("gpu-box saved")

    until(lambda: yaml.safe_load(cfg.read_text(encoding="utf-8"))["machines"]["gpu-box"]
          .get("description") == "RTX 4090", what="the edited machine")
    saved = yaml.safe_load(cfg.read_text(encoding="utf-8"))["machines"]["gpu-box"]
    assert saved.get("exclusive") is True, f"the edit dropped the exclusive queue: {saved}"


def test_a_typed_machine_name_reaches_the_server_whole(ui, ui_page):
    """The save built its URL from the raw name while `test` and `delete` encoded theirs, so a
    `#` or `?` in a typed name cut the path short: "gpu#box" was saved as a machine called
    "gpu", and the server's own name check never saw the name the operator typed."""
    ui_page.goto(f"{ui.url}/#/settings?section=machines")
    ui_page.locator('[data-add="machine"] summary').click()
    ui_page.get_by_placeholder("name (gpu-box)").fill("gpu#box")
    ui_page.get_by_placeholder("host / IP").fill("10.0.0.9")
    ui_page.get_by_placeholder("ssh user").fill("rsched")
    ui_page.get_by_role("button", name="save machine").click()
    expect(ui_page.locator("#toast.err:not([hidden])")).to_contain_text("machine name must be")
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))
    assert not raw.get("machines"), f"a truncated name was saved: {raw.get('machines')}"


def test_routine_machine_binding(ui, ui_page):
    """Binding a catalog machine on the routine page is a change in its settings draft; the one
    accept writes routine.yaml `machines:`."""
    mac = MachineConfig(host="10.0.0.9", user="rsched", description="RTX 4090", tags=["gpu"])
    mac.name = "gpu-box"
    ui.server_cfg.machines = {"gpu-box": mac}   # the live server the API reads

    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    # the machine's checkbox is inside its label row
    row = ui_page.locator("#sec-machines + .panel label", has_text="gpu-box")
    row.wait_for()
    row.locator("input[type=checkbox]").check()
    expect(ui_page.locator("#sec-machines + .panel button", has_text="save")).to_have_count(0)
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("accepted")

    path = ui.routine_dir("uir") / "routine.yaml"
    until(lambda: yaml.safe_load(path.read_text(encoding="utf-8")).get("machines")
          == ["gpu-box"], what="the accepted machine")


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

    # the panel's OWN disclosure: `> summary`, because each abilities card inside it now
    # folds its met requirements behind a "N requirements · all met" summary of its own
    # (components/abilities.js), so a descendant match is no longer unique.
    ui_page.locator(".conv-caps > summary").click()   # ⚙ capabilities & budgets
    row = ui_page.locator("label", has_text="gpu-box")
    row.wait_for()
    row.locator("input[type=checkbox]").check()
    ui_page.get_by_role("button", name="save machines").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("machines saved")

    raw = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["machines"] == ["gpu-box"]
