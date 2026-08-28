#!/usr/bin/env bash
# Bundle the whole rsched STATE — everything that is NOT in the container image — into one archive,
# to migrate the system to another host. Home-relative paths, so it unpacks under any RSCHED_HOME.
#
#   deploy/bundle.sh [OUTFILE]
#
# This is a ONE-SHOT MIGRATION tool, not a backup. The archive is a frozen snapshot, and
# `routines` and `conversations` are rewritten by every run — DOCKER.md's flow ends by
# decommissioning the source host, which is the only reason staleness does not matter there.
# For recurring protection use deploy/backup.sh, which mirrors the same inventory incrementally.
#
# WARNING: the archive includes ~/.credentials (API keys + the Claude OAuth token). Move it over
# scp/ssh only; never commit it or put it anywhere world-readable.
set -euo pipefail

OUT="${1:-${HOME}/rsched-migration-$(date +%Y%m%d-%H%M%S).tgz}"

# shellcheck source=deploy/state-paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/state-paths.sh"
rsched_collect_state_paths

TAR_EXCLUDES=()
for x in "${STATE_EXCLUDES[@]}"; do TAR_EXCLUDES+=("--exclude=${x}"); done

WARNFILE="$(mktemp)"
trap 'rm -f "${WARNFILE}"' EXIT

echo "bundling state → ${OUT}"

# tar exits 1 for WARNINGS and >=2 for a real failure, and on a live instance warnings are the
# NORM: the chrome sidecar rewrites its profile continuously, so files change or vanish between
# tar's stat and its read. Under `set -e` that turned a complete archive into a failed run that
# printed no summary. Distinguish the two — and never let a warning pass unremarked, because a
# file that moved under tar is a file whose copy may be torn.
set +e
tar czf "${OUT}" -C "${HOME}" "${TAR_EXCLUDES[@]}" "${STATE_PATHS[@]}" 2>"${WARNFILE}"
rc=$?
set -e

if [ "${rc}" -ge 2 ]; then
  echo "tar FAILED (exit ${rc}) — the archive at ${OUT} is NOT usable:" >&2
  sed 's/^/  /' "${WARNFILE}" >&2
  exit "${rc}"
fi

echo
echo "bundle ready: ${OUT}  ($(du -h "${OUT}" | cut -f1))"
echo "top-level entries:"; tar tzf "${OUT}" | awk -F/ '{print $1"/"$2}' | sort -u | sed 's/^/  /'

if [ -s "${WARNFILE}" ]; then
  echo
  echo "⚠  tar read these while they were being written — their copies may be TORN:"
  sed 's/^/     /' "${WARNFILE}"
  echo "   chrome-profile is the one that matters: it is a LevelDB store, and a torn copy"
  echo "   restores as a signed-out browser. For a clean capture, stop the sidecar first:"
  echo "     docker compose stop chrome && deploy/bundle.sh && docker compose start chrome"
fi

echo
echo "⚠  contains ~/.credentials — transfer over scp only, do NOT commit."
echo "   next:  scp \"${OUT}\" <user>@192.168.0.128:~/"
echo "          then follow deploy/DOCKER.md on the server."
