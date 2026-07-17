"""The scheduled library-sync daemon job (library_sync.py): export mirrors + prunes,
config is redacted, git sync commits, run_sync records its outcome and never raises."""

import subprocess

import yaml

from rsched import library_sync
from rsched.config import ServerConfig


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.libraries_home = tmp_path / "library"
    s.source = tmp_path / "config.yaml"
    return s


def _mk_routine_tree(home):
    keep = ["routine.yaml", "main.md", "stages/one.md", "state/phase.json"]
    drop = ["status.json", "runs/2026-01-01T00-00-00/transcript.jsonl", "inbox/msg.json",
            "questions/pending/q.json", ".git/HEAD"]
    for rel in keep + drop:
        p = home / "demo" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"content of {rel}\n", encoding="utf-8")
    (home / ".control").mkdir(parents=True, exist_ok=True)
    return keep, drop


def test_export_mirrors_prunes_and_skips_transients(tmp_path):
    home = tmp_path / "routines"
    keep, drop = _mk_routine_tree(home)
    dest = tmp_path / "library" / "routines"
    result = library_sync.export_routines(home, dest)
    for rel in keep:
        assert (dest / "demo" / rel).is_file(), rel
    for rel in drop:
        assert not (dest / "demo" / rel).exists(), rel
    assert not (dest / ".control").exists()                    # dot-dirs are not routines
    assert result["skipped"] == [".control"]
    # rsync-like: delete at the source → pruned from the mirror; unchanged files untouched
    (home / "demo" / "stages" / "one.md").unlink()
    second = library_sync.export_routines(home, dest)
    assert not (dest / "demo" / "stages").exists()
    assert second["removed"] == 1


def test_config_export_redacts_secrets(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text('bind: 127.0.0.1\ntoken: "super-secret"\n'
                   "endpoints:\n  or:\n    kind: openai\n    api_key: sk-live-123\n"
                   '  local:\n    kind: openai\n    api_key: ""\n', encoding="utf-8")
    out = library_sync.export_config(cfg, tmp_path / "dest")
    data = yaml.safe_load((tmp_path / "dest" / "config.yaml").read_text(encoding="utf-8"))
    assert data["token"] == "REDACTED"
    assert data["endpoints"]["or"]["api_key"] == "REDACTED"
    assert data["endpoints"]["local"]["api_key"] == ""         # empty stays empty
    assert data["bind"] == "127.0.0.1" and out["redacted_values"] == 2


def test_export_redacts_trigger_tokens_in_routine_yaml(tmp_path):
    """routine.yaml carries webhook trigger tokens now — the export must never push them
    to a (possibly remote) library repo. Same _redact pass the config export gets."""
    home = tmp_path / "routines"
    d = home / "hooked"
    d.mkdir(parents=True)
    d.joinpath("routine.yaml").write_text(yaml.safe_dump({
        "slug": "hooked", "description": "hook test",
        "triggers": [{"id": "t-1", "type": "webhook", "token": "supersecret",
                      "cooldown_s": 60}],
    }), encoding="utf-8")
    dest = tmp_path / "dest"
    library_sync.export_routines(home, dest)
    text = (dest / "hooked" / "routine.yaml").read_text(encoding="utf-8")
    assert "supersecret" not in text
    assert yaml.safe_load(text)["triggers"][0]["token"] == "REDACTED"


def test_run_sync_ok_writes_status_and_is_idempotent(tmp_path):
    s = _server(tmp_path)
    _mk_routine_tree(s.routines_home)
    s.libraries_home.mkdir()
    subprocess.run(["git", "-C", str(s.libraries_home), "init", "-q", "-b", "main"], check=True)
    s.source.write_text('token: "t0p"\n', encoding="utf-8")
    result = library_sync.run_sync(s)
    assert result["status"] == "ok", result
    assert result["sync"]["committed"] is True and result["sync"]["has_remote"] is False
    assert library_sync.read_status(s)["status"] == "ok"
    exported = yaml.safe_load(
        (s.libraries_home / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert exported["token"] == "REDACTED"
    second = library_sync.run_sync(s)                          # nothing changed → no commit
    assert second["status"] == "ok" and second["sync"]["had_changes"] is False


def test_run_sync_error_is_contained_and_logged(tmp_path):
    s = _server(tmp_path)                                      # library repo does not exist
    s.routines_home.mkdir(parents=True)
    result = library_sync.run_sync(s)
    assert result["status"] == "error" and "does not exist" in result["error"]
    assert library_sync.read_status(s)["status"] == "error"
    events = (s.routines_home / ".control" / "health-events.jsonl").read_text(encoding="utf-8")
    assert "library_sync_error" in events
