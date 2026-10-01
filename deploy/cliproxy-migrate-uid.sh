#!/usr/bin/env bash
# MIGRATION(expires=2026-11-15) — ONE-SHOT: move an existing cliproxy install off root.
#
# Until 0.373.1 the `cliproxy` service ran as its image's root and mounted its logins at
# /root/.cli-proxy-api, so every file it wrote under ~/.config/routine-scheduler/cliproxy/auth
# is root:root — a login it minted 0600, which backup.sh (running as the host user) cannot read,
# so that home failed and no night kept a snapshot. docker-compose.yml now runs the proxy as
# ${RSCHED_UID:-1000}:${RSCHED_GID:-1000} with its logins at /CLIProxyAPI/auth. This hands the
# files it already wrote to that uid — ALL of them: a root file the new uid can read but not
# write is a token refresh that cannot be saved — and points the live config's `auth-dir` at
# the new mount. Then recreate the proxy:
#
#   bash deploy/cliproxy-migrate-uid.sh
#   docker compose up -d --no-deps cliproxy
#
# It STOPS the proxy first, and the order is load-bearing: the proxy hot-reloads config.yaml,
# so a running one would follow a changed `auth-dir` to a path its old container does not have
# and drop every login, and a running root proxy could write a new root file after the chown.
# Do it while no run is using the proxy. Safe to run again: each step checks before it acts.
# Root comes from the pinned image itself (`compose run --user 0:0`), so no sudo is needed. Its
# compose commands run bare from the checkout's root, reading the host's `.env` like any other
# (deploy/DOCKER.md), and name `cliproxy`, which selects it whatever profiles that file enables.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${RSCHED_HOME:-/home/mark}/.config/routine-scheduler/cliproxy"
uid="${RSCHED_UID:-1000}"
gid="${RSCHED_GID:-1000}"
old_dir=/root/.cli-proxy-api
new_dir=/CLIProxyAPI/auth                  # docker-compose.yml's auth target = the config's auth-dir

if [ "$(id -u)" != "${uid}" ]; then
  echo "REFUSING: you are uid $(id -u); the proxy and backup.sh run as uid ${uid} (RSCHED_UID)." >&2
  exit 1
fi
if [ ! -f "${state}/config.yaml" ] || [ ! -d "${state}/auth" ]; then
  echo "no proxy state in ${state} — nothing to migrate (a new install runs deploy/cliproxy-init.sh)" >&2
  exit 1
fi
current="$(sed -n 's/^auth-dir:[[:space:]]*//p' "${state}/config.yaml" | tr -d "\"'" | sed 's/[[:space:]]*$//')"
if [ "${current}" != "${old_dir}" ] && [ "${current}" != "${new_dir}" ]; then
  echo "REFUSING: ${state}/config.yaml names auth-dir '${current}', neither ${old_dir} nor" >&2
  echo "          ${new_dir}. Point it at ${new_dir} by hand, with the proxy stopped." >&2
  exit 1
fi

cd "${repo}"
# The daemon a compose command reaches is the calling shell's, not this checkout's host: run from
# a laptop against a mounted server checkout, the stop/chown below would act on the laptop's
# containers while the `sed -i` rewrote the SERVER's config.yaml (deploy/docker-host-guard.sh).
. "$(dirname -- "${BASH_SOURCE[0]}")/docker-host-guard.sh"
require_local_docker_host || exit 4
compose=(docker compose)
echo "stopping cliproxy, so nothing writes as root and nothing reloads mid-move"
"${compose[@]}" stop cliproxy
echo "handing its state to ${uid}:${gid}"
"${compose[@]}" run --rm --no-deps --user 0:0 --entrypoint chown cliproxy \
  -R "${uid}:${gid}" "${new_dir}" /CLIProxyAPI/config.yaml
if [ "${current}" = "${old_dir}" ]; then
  sed -i "s|^auth-dir:.*\$|auth-dir: ${new_dir}|" "${state}/config.yaml"
  echo "auth-dir: ${old_dir} → ${new_dir}"
else
  echo "auth-dir is already ${new_dir}"
fi

# The property all of this is for: backup.sh, running as this uid, can read every file.
strays="$(find "${state}" \( ! -uid "${uid}" -o ! -readable \) -print 2>&1 || true)"
if [ -n "${strays}" ]; then
  echo "NOT DONE — still not ${uid}'s, or unreadable to it:" >&2
  printf '%s\n' "${strays}" | head -20 | sed 's/^/  /' >&2
  exit 1
fi
echo "every file under ${state} is ${uid}'s. Now recreate the proxy as that uid:"
echo "  docker compose up -d --no-deps cliproxy"
