#!/usr/bin/env bash
# Prepare persistent proxy state. Run on the Docker host, from this checkout.
# Refuses to replace an existing configuration or keys; safe to run again.
#
# Shell and coreutils only. It runs on the HOST, where this checkout has no venv of its own —
# a migration bundle excludes `.venv` and the engine's environment lives inside the image — so
# the `.venv/bin/python` it used to start was missing on exactly the hosts that need it.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${RSCHED_HOME:-/home/mark}/.config/routine-scheduler/cliproxy"

umask 077                                    # every file and dir below is the owner's alone
mkdir -p "${state}/auth"
chmod 700 "${state}" "${state}/auth"
if [ -e "${state}/config.yaml" ]; then
  echo "Already configured: ${state}/config.yaml; existing keys preserved."
  exit 0
fi

# 32 random bytes as 43 URL-safe characters (what Python's secrets.token_urlsafe(32) gives).
key() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }
client="$(key)"
management="$(key)"

set -o noclobber                             # never replace a key file, even racing a second run
# Keep the original management key because the proxy hashes its config value at boot.
printf 'CLIPROXY_API_KEY=%s\nCLIPROXY_MANAGEMENT_KEY=%s\n' "${client}" "${management}" \
  > "${state}/client.env"
sed -e "s/REPLACE_CLIENT_KEY/\"${client}\"/" -e "s/REPLACE_MANAGEMENT_KEY/\"${management}\"/" \
  "${repo}/deploy/cliproxy.config.example.yaml" > "${state}/config.yaml"
echo "Created proxy state in ${state}. Keys were not printed."
