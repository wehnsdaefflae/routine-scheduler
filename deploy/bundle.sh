#!/usr/bin/env bash
# Bundle the whole rsched STATE — everything that is NOT in the container image — into one archive,
# to migrate the system to another host. Home-relative paths, so it unpacks under any RSCHED_HOME.
#
#   deploy/bundle.sh [OUTFILE]
#
# WARNING: the archive includes ~/.credentials (API keys + the Claude OAuth token). Move it over
# scp/ssh only; never commit it or put it anywhere world-readable.
set -euo pipefail

OUT="${1:-${HOME}/rsched-migration-$(date +%Y%m%d-%H%M%S).tgz}"

# home-relative so `tar xzf … -C <RSCHED_HOME>` recreates the exact layout the compose file mounts
PATHS=(
  git-repos/routine-scheduler          # the source tree self-audit edits + the daemon runs from
  .config/routine-scheduler            # config.yaml (token, endpoints, source_repo)
  .credentials                         # SECRETS: endpoint keys + claude-code OAuth token
  routines                             # the routine repos, their runs, state, ledgers
  .local/share/workflow-library        # workflows library (git)
  .local/share/routine-fragments       # fragments library (git)
  .local/share/global-utils            # global utils library (git)
)

for p in "${PATHS[@]}"; do
  [ -e "${HOME}/${p}" ] || { echo "MISSING: ${HOME}/${p} — run deploy/install.sh first?" >&2; exit 1; }
done

echo "bundling state → ${OUT}"
tar czf "${OUT}" -C "${HOME}" \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='node_modules' \
  "${PATHS[@]}"

echo
echo "bundle ready: ${OUT}  ($(du -h "${OUT}" | cut -f1))"
echo "top-level entries:"; tar tzf "${OUT}" | awk -F/ '{print $1"/"$2}' | sort -u | sed 's/^/  /'
echo
echo "⚠  contains ~/.credentials — transfer over scp only, do NOT commit."
echo "   next:  scp \"${OUT}\" <user>@192.168.0.128:~/"
echo "          then follow deploy/DOCKER.md on the server."
