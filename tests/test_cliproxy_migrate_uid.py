"""`deploy/cliproxy-migrate-uid.sh` — the one-shot move of a cliproxy install off root.

It edits a live config and hands credentials to another owner, so the properties that matter are
pinned here against a stub `docker`: the proxy is STOPPED before `auth-dir` changes (a running
proxy hot-reloads its config and would drop every login), the chown reaches exactly the paths
compose mounts, a config it does not recognise is left alone, and it does not claim success while
any file is still unreadable to the backup. Delete this file with the script.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy/cliproxy-migrate-uid.sh"
OLD, NEW = "/root/.cli-proxy-api", "/CLIProxyAPI/auth"
CONFIG = 'host: ""\nport: 8317\nauth-dir: {}\napi-keys:\n  - "k"\n'


def _state(tmp_path: Path, auth_dir: str = OLD) -> Path:
    state = tmp_path / "data/.config/routine-scheduler/cliproxy"
    (state / "auth/logs").mkdir(parents=True)
    (state / "auth/claude-someone.json").write_text("{}", encoding="utf-8")
    (state / "auth/logs/error-v1-messages.log").write_text("", encoding="utf-8")
    config = state / "config.yaml"
    config.write_text(CONFIG.format(auth_dir), encoding="utf-8")
    config.chmod(0o600)
    return state


def _migrate(tmp_path: Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    """One run against the throwaway data home, through a `docker` that records each call with
    the config's auth-dir line AS IT STOOD at that moment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    stub = bin_dir / "docker"
    stub.write_text('#!/bin/sh\nprintf "%s | %s\\n" "$*" "$(grep "^auth-dir" "$STUB_CONFIG")" '
                    '>> "$STUB_LOG"\n', encoding="utf-8")
    stub.chmod(0o755)
    data = tmp_path / "data"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RSCHED_HOME": str(data),
           "RSCHED_UID": str(os.getuid()), "RSCHED_GID": str(os.getgid()),
           "STUB_LOG": str(log),
           "STUB_CONFIG": str(data / ".config/routine-scheduler/cliproxy/config.yaml")}
    proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                          timeout=60, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


def test_it_stops_the_proxy_hands_over_its_files_then_moves_auth_dir(tmp_path):
    state = _state(tmp_path)
    proc, calls = _migrate(tmp_path)
    assert proc.returncode == 0, proc.stderr
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    assert calls == [
        f"compose stop cliproxy | auth-dir: {OLD}",
        (f"compose run --rm --no-deps --user 0:0 --entrypoint chown "
         f"cliproxy -R {uid_gid} {NEW} /CLIProxyAPI/config.yaml | auth-dir: {OLD}"),
    ]
    config = state / "config.yaml"
    assert config.read_text(encoding="utf-8") == CONFIG.format(NEW)   # one line, nothing else
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "up -d --no-deps cliproxy" in proc.stdout                  # the next step, named


def test_it_chowns_exactly_what_compose_mounts_and_moves_auth_dir_to_its_target():
    """The script spells the container paths; compose decides them. One must follow the other."""
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    targets = {v["target"] for v in compose["services"]["cliproxy"]["volumes"]}
    script = SCRIPT.read_text(encoding="utf-8")
    assert targets == {NEW, "/CLIProxyAPI/config.yaml"}
    assert f"new_dir={NEW} " in script and '"${new_dir}" /CLIProxyAPI/config.yaml' in script


def test_a_second_run_changes_nothing_it_already_changed(tmp_path):
    state = _state(tmp_path, auth_dir=NEW)
    proc, _ = _migrate(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "already" in proc.stdout
    assert (state / "config.yaml").read_text(encoding="utf-8") == CONFIG.format(NEW)


def test_an_auth_dir_it_does_not_know_is_left_alone(tmp_path):
    state = _state(tmp_path, auth_dir="/srv/elsewhere")
    proc, calls = _migrate(tmp_path)
    assert proc.returncode == 1
    assert "/srv/elsewhere" in proc.stderr
    assert calls == []                                       # the proxy was not even stopped
    assert (state / "config.yaml").read_text(encoding="utf-8") == CONFIG.format("/srv/elsewhere")


def test_it_does_not_claim_success_while_a_file_is_unreadable(tmp_path):
    state = _state(tmp_path)
    stuck = state / "auth/.oauth-anthropic-0123.oauth"
    stuck.write_text("{}", encoding="utf-8")
    stuck.chmod(0)
    try:
        proc, _ = _migrate(tmp_path)
    finally:
        stuck.chmod(0o600)
    assert proc.returncode == 1
    assert "NOT DONE" in proc.stderr and stuck.name in proc.stderr
