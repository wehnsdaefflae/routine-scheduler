#!/usr/bin/env bash
# Idempotent install: venv, config+token, dirs, workflow library seed, systemd user
# service with linger. Safe to re-run after every git pull.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/routine-scheduler"
CONFIG="${CONFIG_DIR}/config.yaml"
ROUTINES="${HOME}/routines"
LIBRARY="${HOME}/.local/share/workflow-library"
UNIT_DIR="${HOME}/.config/systemd/user"

echo "== rsched install (${REPO})"

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
(cd "${REPO}" && uv sync --quiet)
echo "venv synced"

mkdir -p "${ROUTINES}" "${CONFIG_DIR}"

if [ ! -f "${CONFIG}" ]; then
  TOKEN="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  sed "s/token: \"change-me\".*/token: \"${TOKEN}\"/" "${REPO}/config/config.example.yaml" > "${CONFIG}"
  echo "config written: ${CONFIG} (token generated)"
else
  echo "config exists: ${CONFIG}"
fi

# Workflow library: seed once, git-init with best-effort auto-push hook.
if [ -d "${REPO}/library-seed" ] && [ -n "$(find "${REPO}/library-seed" -type f -print -quit)" ] \
    && [ ! -d "${LIBRARY}" ]; then
  mkdir -p "${LIBRARY}"
  cp -r "${REPO}/library-seed/." "${LIBRARY}/"
  git -C "${LIBRARY}" init -q -b main
  git -C "${LIBRARY}" config user.name "routine-scheduler"
  git -C "${LIBRARY}" config user.email "noreply@routine-scheduler.local"
  git -C "${LIBRARY}" add -A
  git -C "${LIBRARY}" commit -qm "seed workflow library"
  echo "workflow library seeded: ${LIBRARY}"
fi
if [ -d "${LIBRARY}/.git" ]; then
  install -m 0755 "${REPO}/deploy/post-commit" "${LIBRARY}/.git/hooks/post-commit"
fi

# systemd user service + linger (so the daemon survives logout / starts at boot).
mkdir -p "${UNIT_DIR}"
install -m 0644 "${REPO}/deploy/routine-scheduler.service" "${UNIT_DIR}/routine-scheduler.service"
systemctl --user daemon-reload
systemctl --user enable --now routine-scheduler.service
loginctl enable-linger "$(whoami)" 2>/dev/null || \
  echo "NOTE: 'loginctl enable-linger $(whoami)' failed — run it with sudo once."

# Ollama context note (its default num_ctx truncates long prompts regardless of model).
if systemctl is-active --quiet ollama 2>/dev/null || pgrep -x ollama >/dev/null 2>&1; then
  echo "NOTE: for local Ollama endpoints set OLLAMA_CONTEXT_LENGTH=16384 (e.g. via"
  echo "      'sudo systemctl edit ollama' → [Service] Environment=OLLAMA_CONTEXT_LENGTH=16384)"
fi

PORT="$(grep -oP '^port: \K[0-9]+' "${CONFIG}" || echo 8321)"
TOKEN="$(grep -oP '^token: "\K[^"]+' "${CONFIG}")"
echo
echo "== done. Web UI: http://127.0.0.1:${PORT}  ·  token: ${TOKEN}"
systemctl --user --no-pager status routine-scheduler.service | head -5
