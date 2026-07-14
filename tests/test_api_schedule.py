"""Week-strip schedule endpoint: fire enumeration, filtering, clamping, bad crons."""

from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import load_server_config
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="weekly")  # cron "0 7 * * 1" via the shared fixture
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "system_model": {"endpoint": "dummy", "model": "m"},
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, tmp_path


def _set_schedule(routines, slug, **schedule):
    p = routines / slug / "routine.yaml"
    cfg = yaml.safe_load(p.read_text())
    cfg.update(schedule)
    p.write_text(yaml.safe_dump(cfg))


def test_weekly_routine_fires_in_window(client):
    c, _ = client
    data = c.get("/api/schedule/week").json()
    assert data["days"] == 7
    rows = {r["slug"]: r for r in data["routines"]}
    assert set(rows) == {"weekly"}
    # a day of back-fill + 7 days ahead: a weekly cron lands 1-2 times, all ISO-parseable
    fires = [datetime.fromisoformat(t) for t in rows["weekly"]["fires"]]
    assert 1 <= len(fires) <= 2 and not rows["weekly"]["truncated"]
    assert all(t.tzinfo is not None for t in fires)


def test_hourly_fires_and_days_clamp(client, make_routine):
    c, tmp = client
    make_routine(slug="hourly")
    _set_schedule(tmp / "routines", "hourly", schedule={"cron": "0 * * * *", "tz": "Europe/Berlin"})
    data = c.get("/api/schedule/week?days=99").json()
    assert data["days"] == 14
    rows = {r["slug"]: r for r in data["routines"]}
    assert 15 * 24 - 2 <= len(rows["hourly"]["fires"]) <= 400  # 14d + back-fill, capped


def test_manual_disabled_and_broken_are_skipped(client, make_routine):
    c, tmp = client
    for slug, patch in [("manual", {"schedule": {"cron": ""}}),
                        ("off", {"enabled": False}),
                        ("broken", {"schedule": {"cron": "not a cron"}})]:
        make_routine(slug=slug)
        _set_schedule(tmp / "routines", slug, **patch)
    rows = {r["slug"] for r in c.get("/api/schedule/week").json()["routines"]}
    assert rows == {"weekly"}
