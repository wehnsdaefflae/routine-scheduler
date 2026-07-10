#!/usr/bin/env bash
# Idempotent install: venv, config+token, dirs, library seed, systemd user service with
# linger. Safe to re-run after every git pull.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/routine-scheduler"
CONFIG="${CONFIG_DIR}/config.yaml"
ROUTINES="${HOME}/routines"
LIBRARIES="${HOME}/.local/share/routine-scheduler-libraries"
UNIT_DIR="${HOME}/.config/systemd/user"

echo "== rsched install (${REPO})"

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
(cd "${REPO}" && uv sync --quiet)
echo "venv synced"

mkdir -p "${ROUTINES}" "${CONFIG_DIR}"

if [ ! -f "${CONFIG}" ]; then
  TOKEN="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")"
  sed "s/token: \"change-me\".*/token: \"${TOKEN}\"/" "${REPO}/config/config.example.yaml" > "${CONFIG}"
  echo "config written: ${CONFIG} (token generated)"
else
  echo "config exists: ${CONFIG}"
fi

# The library — ONE git repo holding workflows/, fragments/ and utils/. Seed once, git-init
# with best-effort auto-push hook. The `gu` dispatcher is installed by the engine
# (utils_lib.ensure_library) on first use.
if [ ! -d "${LIBRARIES}" ]; then
  mkdir -p "${LIBRARIES}/fragments" "${LIBRARIES}/utils"
  [ -d "${REPO}/library-seed/workflows" ] && cp -r "${REPO}/library-seed/workflows" "${LIBRARIES}/workflows"
  cp "${REPO}/library-seed/fragments/"*.md "${LIBRARIES}/fragments/" 2>/dev/null || true
  [ -d "${REPO}/util-seed/utils" ] && cp -r "${REPO}/util-seed/utils/." "${LIBRARIES}/utils/"
  git -C "${LIBRARIES}" init -q -b main
  git -C "${LIBRARIES}" config user.name "routine-scheduler"
  git -C "${LIBRARIES}" config user.email "noreply@routine-scheduler.local"
  git -C "${LIBRARIES}" add -A
  git -C "${LIBRARIES}" commit -qm "seed library repo"
  echo "library seeded: ${LIBRARIES}"
fi
if [ -d "${LIBRARIES}/.git" ]; then
  install -m 0755 "${REPO}/deploy/post-commit" "${LIBRARIES}/.git/hooks/post-commit"
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
