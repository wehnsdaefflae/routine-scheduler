"""`deploy/cliproxy-init.sh`, run the way docs/claude-proxy-cutover.md runs it.

It runs on the Docker HOST, from the source checkout — and a checkout there has no venv of its
own: the migration bundle excludes `.venv` and the engine's environment lives inside the image.
So the test runs a copy of the script from a checkout with nothing beside it but its example
config, against a throwaway data home.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _init(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    checkout = tmp_path / "checkout"
    (checkout / "deploy").mkdir(parents=True, exist_ok=True)
    for name in ("cliproxy-init.sh", "cliproxy.config.example.yaml"):
        (checkout / "deploy" / name).write_bytes((DEPLOY / name).read_bytes())
    data = tmp_path / "data"
    proc = subprocess.run(["bash", str(checkout / "deploy/cliproxy-init.sh")],
                          env={"PATH": "/usr/bin:/bin", "RSCHED_HOME": str(data)},
                          capture_output=True, text=True, timeout=60, check=False)
    return proc, data / ".config/routine-scheduler/cliproxy"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_bare_checkout_creates_private_state_with_fresh_keys(tmp_path):
    proc, state = _init(tmp_path)
    assert proc.returncode == 0, proc.stderr
    keys = dict(line.split("=", 1) for line in (state / "client.env").read_text().splitlines())
    client, management = keys["CLIPROXY_API_KEY"], keys["CLIPROXY_MANAGEMENT_KEY"]
    assert client != management and min(len(client), len(management)) >= 43
    config = yaml.safe_load((state / "config.yaml").read_text())
    assert config["api-keys"] == [client]
    assert config["remote-management"]["secret-key"] == management
    assert "REPLACE_" not in (state / "config.yaml").read_text()
    assert client not in proc.stdout and management not in proc.stdout   # never printed
    assert {_mode(state), _mode(state / "auth")} == {0o700}
    assert {_mode(state / "config.yaml"), _mode(state / "client.env")} == {0o600}


def test_a_second_run_keeps_the_keys_it_made(tmp_path):
    first, state = _init(tmp_path)
    assert first.returncode == 0, first.stderr
    before = {p.name: p.read_bytes() for p in (state / "config.yaml", state / "client.env")}
    second, _ = _init(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "Already configured" in second.stdout
    assert {p.name: p.read_bytes() for p in (state / "config.yaml", state / "client.env")} == before
