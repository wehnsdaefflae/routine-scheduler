"""Boot-time recovery of wizard builds orphaned by a server restart/crash (finalize.json
stuck at 'building'). See wizard_store.recover_orphan_builds."""

from rsched.config import ServerConfig
from rsched.paths import atomic_write_json, read_json
from rsched.web import wizard_store


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True)
    return s


def _wizard(server, wid: str, finalize: dict | None) -> "Path":
    d = server.routines_home / wid
    (d / "state").mkdir(parents=True)
    if finalize is not None:
        atomic_write_json(d / "state" / "finalize.json", finalize)
    return d


def test_recovers_orphaned_building_and_cleans_half_built_dir(tmp_path):
    server = _server(tmp_path)
    wid = ".wizard-orphan"
    _wizard(server, wid, {"state": "building", "slug": "config-optimizer"})
    # half-scaffolded routine dir: subdirs exist but routine.yaml never got written
    partial = server.routines_home / "config-optimizer"
    (partial / "state").mkdir(parents=True)
    (partial / "steps").mkdir()

    recovered = wizard_store.recover_orphan_builds(server)

    assert recovered == [wid]
    assert not partial.exists()  # half-built dir removed
    fin = read_json(server.routines_home / wid / "state" / "finalize.json")
    assert fin["state"] == "error"
    assert fin["slug"] == "config-optimizer"
    assert "restart" in fin["error"].lower()


def test_preserves_fully_scaffolded_routine_with_yaml(tmp_path):
    server = _server(tmp_path)
    wid = ".wizard-late"
    _wizard(server, wid, {"state": "building", "slug": "built"})
    built = server.routines_home / "built"
    built.mkdir()
    (built / "routine.yaml").write_text("name: Built\n", encoding="utf-8")

    recovered = wizard_store.recover_orphan_builds(server)

    assert recovered == [wid]
    assert built.exists() and (built / "routine.yaml").exists()  # a completed scaffold is kept
    assert read_json(built.parent / wid / "state" / "finalize.json")["state"] == "error"


def test_leaves_done_and_plain_sessions_untouched(tmp_path):
    server = _server(tmp_path)
    _wizard(server, ".wizard-done", {"state": "done", "slug": "made", "run_id": None})
    _wizard(server, ".wizard-clarify", None)  # no finalize yet (still clarifying)

    recovered = wizard_store.recover_orphan_builds(server)

    assert recovered == []
    assert read_json(server.routines_home / ".wizard-done" / "state" / "finalize.json")["state"] == "done"


def test_missing_home_is_safe(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "nope"
    assert wizard_store.recover_orphan_builds(server) == []
