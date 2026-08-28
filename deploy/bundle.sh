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
  .local/share/routine-scheduler-libraries   # the library repo: workflows/ + fragments/ + utils/ (git)
)

# State a SIDECAR writes: real data, but it only exists once that sidecar has run, so its absence
# is a fresh install rather than a broken one. Listed apart from PATHS so the difference is
# declared instead of inferred from a missing-file fallback.
OPTIONAL_PATHS=(
  chrome-profile                       # the logged-in browser sessions (docs/browser-sessions.md)
)

for p in "${PATHS[@]}"; do
  [ -e "${HOME}/${p}" ] || { echo "MISSING: ${HOME}/${p} — run deploy/install.sh first?" >&2; exit 1; }
done

for p in "${OPTIONAL_PATHS[@]}"; do
  if [ -e "${HOME}/${p}" ]; then
    PATHS+=("${p}")
  else
    echo "skipping ${p} (sidecar has not run here)"
  fi
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
