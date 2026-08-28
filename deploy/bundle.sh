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

# home-relative so `tar xzf … -C <RSCHED_HOME>` recreates the exact layout the compose file mounts.
#
# THE INVARIANT: every bind mount in docker-compose.yml that holds DATA appears in one of the two
# lists below. A data home that is bind-mounted but unbundled dies on the migration instead of on
# the recreate — the same loss, one host later — so the two files are read together.
PATHS=(
  git-repos/routine-scheduler          # the source tree self-audit edits + the daemon runs from
  .config/routine-scheduler            # config.yaml (token, endpoints, source_repo)
  .credentials                         # SECRETS: endpoint keys + claude-code OAuth token
  routines                             # the routine repos, their runs, state, ledgers
  conversations                        # interactive sessions: routine-shaped, un-versioned, irreplaceable
  background                           # detached background runs a conversation launched, mid-flight
  .local/share/routine-scheduler-libraries   # the library repo: workflows/ + fragments/ + utils/ (git)
)

# State that only exists once a FEATURE has been used — a sidecar that has run, a messenger that
# has been paired. Real data, but its absence is a fresh install rather than a broken one. Listed
# apart from PATHS so the difference is declared instead of inferred from a missing-file fallback.
OPTIONAL_PATHS=(
  chrome-profile                       # the logged-in browser sessions (docs/browser-sessions.md)
  telegram-sessions                    # a LINKED SESSION is the credential — there is no API key to
  signal-sessions                      # re-enter, so losing one of these unlinks the account and
  whatsapp-sessions                    # the operator has to re-pair by phone
  .config/gh                           # `gh auth login`'s token, re-mintable only by a device flow
)

# Deliberately NOT bundled, so their absence is a decision and not an oversight:
#   .cache/ms-playwright  — a ~170 MB browser download the `page-fetch` util re-fetches on first
#                           use. Bind-mounted to survive a RECREATE, worthless in a tarball.
#   tor-data (volume)     — Tor's guard/consensus state: regenerable, and meaningless on a new host.

for p in "${PATHS[@]}"; do
  [ -e "${HOME}/${p}" ] || { echo "MISSING: ${HOME}/${p} — run deploy/install.sh first?" >&2; exit 1; }
done

for p in "${OPTIONAL_PATHS[@]}"; do
  if [ -e "${HOME}/${p}" ]; then
    PATHS+=("${p}")
  else
    echo "skipping ${p} (feature never used on this host)"
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
