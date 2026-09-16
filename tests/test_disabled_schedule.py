"""Disabled schedules block new starts and migrate without losing configuration."""
import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.migrate_disabled_schedule import run


def test_disabled_migration_is_idempotent_and_preserves_cadence(make_routine):
    root = make_routine(slug="disabled")
    path = root / "routine.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["enabled"] = False
    original_schedule = dict(raw["schedule"])
    path.write_text(yaml.safe_dump(raw))
    assert run(root.parent) == 1
    assert run(root.parent) == 0
    saved = yaml.safe_load(path.read_text())
    assert "enabled" not in saved
    assert saved["schedule"] == {**original_schedule, "disabled": True}
    cfg, problems = load_routine(root)
    assert not problems
    assert cfg.enabled is False


@pytest.mark.parametrize("reason", ["manual", "schedule", "catchup", "webhook", "report", "lane", "one-shot"])
async def test_disabled_refuses_every_new_start_without_creating_run(make_routine, reason):
    root = make_routine(slug="disabled")
    path = root / "routine.yaml"
    raw = yaml.safe_load(path.read_text())
    raw.pop("enabled", None)
    raw["schedule"]["disabled"] = True
    path.write_text(yaml.safe_dump(raw))
    cfg, problems = load_routine(root)
    assert not problems
    runner = Runner(ServerConfig(routines_home=root.parent), EventBus())
    assert await runner.fire(cfg, reason=reason) is None
    assert not runner.active
    assert not (root / "runs").exists()



def test_patching_enabled_reaches_the_firing_gate_instead_of_a_dead_key(api_client, make_routine):
    """The D72 dashboard toggle PATCHes `enabled`; the firing gate reads `schedule.disabled`.

    Before F448 the key had no applier, so it fell through the catch-all and wrote a bare
    `enabled:` the gate never reads: the toggle reported success, the commit message named the
    field, and the routine kept firing. The PATCH must land where the gate actually looks.
    """
    c, _ = api_client
    root = make_routine(slug="apir")
    path = root / "routine.yaml"

    r = c.patch("/api/routines/apir", json={"enabled": False})
    assert r.status_code == 200
    assert "enabled" in r.json()["updated"]          # R102: reported applied...
    raw = yaml.safe_load(path.read_text())
    assert raw["schedule"]["disabled"] is True       # ...and actually applied
    assert "enabled" not in raw                      # no dead key left behind

    cfg, problems = load_routine(root)
    assert not problems and cfg.enabled is False

    r = c.patch("/api/routines/apir", json={"enabled": True})
    assert r.status_code == 200
    raw = yaml.safe_load(path.read_text())
    assert raw["schedule"]["disabled"] is False
    assert "enabled" not in raw
    cfg, problems = load_routine(root)
    assert not problems and cfg.enabled is True
