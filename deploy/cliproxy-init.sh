#!/usr/bin/env bash
# Prepare persistent proxy state. Run on the Docker host, from this checkout.
# Refuses to replace an existing configuration or keys; safe to run again.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"${repo}/.venv/bin/python" - "${repo}" "${RSCHED_HOME:-/home/mark}" <<'PY'
import os
import secrets
import sys
from pathlib import Path

import yaml

repo, data_home = map(Path, sys.argv[1:])
state = data_home / ".config/routine-scheduler/cliproxy"
state.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(state, 0o700)
(state / "auth").mkdir(exist_ok=True, mode=0o700)
target = state / "config.yaml"
if target.exists():
    print(f"Already configured: {target}; existing keys preserved.")
    raise SystemExit(0)
config = yaml.safe_load((repo / "deploy/cliproxy.config.example.yaml").read_text())
client = secrets.token_urlsafe(32)
management = secrets.token_urlsafe(32)
config["api-keys"] = [client]
config["remote-management"]["secret-key"] = management
# Keep the original management key because the proxy hashes its config value at boot.
with os.fdopen(os.open(state / "client.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
    out.write(f"CLIPROXY_API_KEY={client}\nCLIPROXY_MANAGEMENT_KEY={management}\n")
with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
    yaml.safe_dump(config, out, sort_keys=False)
print(f"Created proxy state in {state}. Keys were not printed.")
PY
