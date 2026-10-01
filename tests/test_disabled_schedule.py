"""The off switch has ONE spelling — the top-level `enabled: false` every reader reads.

It had two: the console's pause toggle, its "Disabled" cadence and goal retirement wrote
`schedule.disabled`, while creation, patterns and the conversation and background homes wrote
`enabled`, and the loader folded the first into the second and gave it the final word. Which
key a file's switch lived in depended on which door last touched it.
"""
import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.patterns import fields


def _raw(root):
    return yaml.safe_load((root / "routine.yaml").read_text(encoding="utf-8"))


def _write(root, raw):
    (root / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


@pytest.mark.parametrize("reason", ["manual", "schedule", "catchup", "webhook", "report", "lane", "one-shot"])
async def test_switched_off_refuses_every_new_start_without_creating_run(make_routine, reason):
    root = make_routine(slug="disabled")
    _write(root, {**_raw(root), "enabled": False})
    cfg, problems = load_routine(root)
    assert not problems and cfg.enabled is False
    runner = Runner(ServerConfig(routines_home=root.parent), EventBus())
    assert await runner.fire(cfg, reason=reason) is None
    assert not runner.active
    assert not (root / "runs").exists()


def test_the_pause_toggle_writes_the_key_every_reader_reads(api_client, make_routine):
    """The dashboard's ⏸/▷ and goal retirement PATCH `enabled`; it lands as `enabled`, and the
    schedule mapping holds no second opinion beside it."""
    c, _ = api_client
    root = make_routine(slug="apir")

    r = c.patch("/api/routines/apir", json={"enabled": False})
    assert r.status_code == 200 and "enabled" in r.json()["updated"]
    raw = _raw(root)
    assert raw["enabled"] is False and "disabled" not in raw["schedule"]
    assert load_routine(root)[0].enabled is False

    assert c.patch("/api/routines/apir", json={"enabled": True}).status_code == 200
    assert _raw(root)["enabled"] is True and load_routine(root)[0].enabled is True
    # strict: a string is refused rather than read as true
    assert c.patch("/api/routines/apir", json={"enabled": "no"}).status_code == 422


def test_the_disabled_cadence_is_the_same_switch(api_client, make_routine):
    """The Schedule panel leads with "Disabled": choosing it switches the routine off in the
    one key, and choosing a real cadence switches it back on."""
    c, _ = api_client
    root = make_routine(slug="apir")

    r = c.patch("/api/routines/apir", json={"schedule": {"friendly": {"frequency": "disabled"}}})
    assert r.status_code == 200
    raw = _raw(root)
    assert raw["enabled"] is False and "disabled" not in raw["schedule"]

    r = c.patch("/api/routines/apir",
                json={"schedule": {"friendly": {"frequency": "daily", "time": "06:30"}}})
    assert r.status_code == 200
    cfg, _problems = load_routine(root)
    assert cfg.enabled is True and cfg.cron == "30 6 * * *"
    # the retired spelling is not a schedule key any more
    assert c.patch("/api/routines/apir",
                   json={"schedule": {"disabled": True}}).status_code == 422


def test_the_settings_accept_switches_off_and_keeps_the_cron(api_client, make_routine):
    """The routine page's one accept routes the cadence through `patch_shape`: "Disabled" is
    the off switch and keeps the cron for the day it is switched back on; a lane member's own
    cadence decides nothing, so all it can say is that the routine is on."""
    c, _ = api_client
    root = make_routine(slug="apir")
    off = {"friendly": {"frequency": "disabled"}, "catchup": "skip"}
    assert fields.patch_shape("schedule", off, lane_managed=False) == ("enabled", False)
    assert fields.patch_shape("schedule", {"friendly": {"frequency": "daily", "time": "07:00"}},
                              lane_managed=True) == ("enabled", True)

    r = c.post("/api/routines/apir/settings", json={"changes": {"schedule": off}})
    assert r.status_code == 200, r.text
    raw = _raw(root)
    assert raw["enabled"] is False and raw["schedule"]["cron"] == "0 7 * * 1"
    assert "disabled" not in raw["schedule"]


def test_a_leftover_schedule_disabled_is_reported_and_read_by_nothing(make_routine):
    """No second spelling survives in the loader: a stray `schedule.disabled` (a hand edit, a
    file the boot migration never saw) decides nothing, and says so."""
    root = make_routine(slug="stale")
    raw = _raw(root)
    raw["schedule"]["disabled"] = True
    _write(root, raw)
    cfg, problems = load_routine(root)
    assert cfg.enabled is True
    assert [p for p in problems if p.startswith("schedule.disabled:")] == [
        ("schedule.disabled: unknown routine.yaml key — a routine is switched off by the "
         "top-level `enabled: false` (ignored)")]
    raw["schedule"] = {**raw["schedule"], "crn": "0 9 * * *"}
    raw["schedule"].pop("disabled")
    _write(root, raw)
    assert "schedule.crn: unknown routine.yaml key — check the spelling (ignored)" in \
        load_routine(root)[1]
