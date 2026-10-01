#!/usr/bin/env bash
# Idempotent install: venv, config + tokens, dirs, library seed, systemd user service with
# linger. Safe to re-run after every git pull.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/routine-scheduler"
ROUTINES="${HOME}/routines"
CONVERSATIONS="${HOME}/conversations"
BACKGROUND="${HOME}/background"
LIBRARIES="${HOME}/.local/share/routine-scheduler-libraries"
UNIT_DIR="${HOME}/.config/systemd/user"
# The two paths the unit runs the daemon from (`%h/…` in deploy/routine-scheduler.service). The
# backup inventory carries the checkout at the first one too. Anywhere else, this script used to
# finish "done" and enable a service that could not start, so it refuses up front instead.
CHECKOUT="${HOME}/git-repos/routine-scheduler"
UV="${HOME}/.local/bin/uv"

echo "== rsched install (${REPO})"

if [ ! "${REPO}" -ef "${CHECKOUT}" ]; then
  echo "This checkout must live at ${CHECKOUT}: the systemd unit, the compose file and the" >&2
  echo "backup inventory all run it from there. Clone or move it there and re-run." >&2
  exit 1
fi
if [ ! -x "${UV}" ]; then
  echo "uv is required at ${UV}, where the systemd unit runs it — the standalone installer" >&2
  echo "puts it there (https://docs.astral.sh/uv/); for a uv installed elsewhere:" >&2
  echo "  ln -s \"\$(command -v uv)\" ${UV}" >&2
  exit 1
fi
(cd "${REPO}" && "${UV}" sync --quiet)
echo "venv synced"

# The three data homes (server config: routines_home / conversations_home / background_home).
# Conversations and background tasks create their own home lazily on first use, but an install
# that has never had either still OWNS those dirs: docker-compose.yml bind-mounts them and
# deploy/bundle.sh requires them, so a fresh install is complete rather than half-migratable.
mkdir -p "${ROUTINES}" "${CONVERSATIONS}" "${BACKGROUND}" "${CONFIG_DIR}"

# The config — written by bootstrap.ensure_config, the ONE implementation the daemon also runs at
# first boot, which gives BOTH bearer tiers a random token. The shell copy that lived here
# replaced the example's `token:` line only, so every host install shipped the example's
# placeholder `routine_token` — a credential anyone could read in this repository.
(cd "${REPO}" && "${UV}" run python -c \
  'from rsched.bootstrap import ensure_config; from rsched.paths import config_file; print("config written (fresh tokens):" if ensure_config() else "config exists:", config_file())')

# The library — ONE git repo holding workflows/, rules/, permissions/, patterns/, reminders/,
# playbooks/ and utils/. Seeded by bootstrap.seed_libraries, which git-inits it and installs the
# best-effort auto-push hook; the `gu` dispatcher is installed by the engine
# (utils_lib.ensure_library) on first use.
#
# This CALLS the one implementation rather than carrying a shell copy of it: a shell copy drifts
# from the library's doc kinds; a host install then starts with kinds missing that only the
# add-only boot sync ever fills in.
if [ ! -d "${LIBRARIES}" ]; then
  (cd "${REPO}" && "${UV}" run python -c \
    'import sys; from pathlib import Path; from rsched.bootstrap import seed_libraries; seed_libraries(Path(sys.argv[1]))' \
    "${LIBRARIES}")
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

# Port and token through the config LOADER, never a grep: the file is YAML, and the first save
# on the Settings page rewrites it with the token unquoted — which the quoted-token grep this
# replaced did not match, failing every re-run of this script at its last line.
read -r PORT TOKEN < <(cd "${REPO}" && "${UV}" run python -c \
  'from rsched.config import load_server_config; cfg, _ = load_server_config(); print(cfg.port, cfg.token)')
echo
echo "== done. Web UI: http://127.0.0.1:${PORT}  ·  token: ${TOKEN}"
# Informational only — `enable --now` above already failed the script if the start failed — so
# the unit's own lines without the journal tail, and never through a pipe that pipefail could
# turn into a failed install after "done" was printed.
systemctl --user --no-pager --lines=0 status routine-scheduler.service || true
