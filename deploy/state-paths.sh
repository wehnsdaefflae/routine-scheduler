#!/usr/bin/env bash
# The rsched STATE INVENTORY — the single list of what is not in the container image.
# Sourced, never executed: `deploy/bundle.sh` (one-shot migration tarball) and
# `deploy/backup.sh` (recurring mirror) both read it, so the two cannot drift.
#
# THE INVARIANT: every bind mount in docker-compose.yml that holds DATA appears in one of
# the two lists below. A data home that is mounted but not listed here dies on a recreate,
# a migration or a disk failure with nothing to catch it — that bug shipped once already.
#
# Paths are HOME-relative, so `tar xzf … -C <RSCHED_HOME>` and an rsync into a mirror root
# both recreate the exact layout the compose file mounts.

# Core data. Absent = a broken install, so a consumer refuses rather than quietly skipping.
STATE_PATHS_REQUIRED=(
  git-repos/routine-scheduler          # the source tree self-audit edits + the daemon runs from
  .config/routine-scheduler            # config.yaml (token, endpoints, source_repo)
  .credentials                         # SECRETS: endpoint keys + claude-code OAuth token
  routines                             # the routine repos, their runs, state, ledgers
  conversations                        # interactive sessions: routine-shaped, un-versioned, irreplaceable
  background                           # detached background runs a conversation launched, mid-flight
  .local/share/routine-scheduler-libraries   # the library repo: workflows/ + rules/ + utils/ (git)
)

# State that only exists once a FEATURE has been used — a sidecar that has run, a messenger
# that has been paired. Real data, but its absence is a fresh install rather than a broken
# one. Listed apart so the difference is declared instead of inferred from a missing file.
STATE_PATHS_OPTIONAL=(
  chrome-profile                       # the logged-in browser sessions (docs/browser-sessions.md)
  telegram-sessions                    # a LINKED SESSION is the credential — there is no API key to
  signal-sessions                      # re-enter, so losing one of these unlinks the account and
  whatsapp-sessions                    # the operator has to re-pair by phone
  .config/gh                           # `gh auth login`'s token, re-mintable only by a device flow
  .claude-daemon                       # the interactive `claude /login` token: the only credential
                                       # with the user:profile scope the quota read needs, and the
                                       # only one nothing can re-mint headlessly
)

# Deliberately NOT carried, so their absence is a decision and not an oversight:
#   .cache/ms-playwright  — a ~170 MB browser download the `page-fetch` util re-fetches on
#                           first use. Bind-mounted to survive a RECREATE, worthless in a copy.
#   tor-data (volume)     — Tor's guard/consensus state: regenerable, meaningless elsewhere.

# Build artefacts and per-boot runtime files: reconstructed on demand, and in the Singleton
# case actively harmful to restore. Chrome writes those three as DANGLING symlinks naming the
# host and pid that hold the profile — rsync copies the link and then fails setting times on a
# target that does not exist (exit 23), and a restored SingletonLock tells a fresh Chrome that
# another instance already owns the profile.
STATE_EXCLUDES=(
  .venv
  __pycache__
  '*.pyc'
  node_modules
  SingletonLock
  SingletonSocket
  SingletonCookie
)

# Populate STATE_PATHS with every home that exists here: the required ones (refusing if one
# is missing) plus whichever optional ones this host has. Reports each skip on stderr so a
# thin backup is visible rather than silent.
rsched_collect_state_paths() {
  STATE_PATHS=()
  local p
  for p in "${STATE_PATHS_REQUIRED[@]}"; do
    [ -e "${HOME}/${p}" ] || {
      echo "MISSING: ${HOME}/${p} — run deploy/install.sh first?" >&2
      return 1
    }
    STATE_PATHS+=("${p}")
  done
  for p in "${STATE_PATHS_OPTIONAL[@]}"; do
    if [ -e "${HOME}/${p}" ]; then
      STATE_PATHS+=("${p}")
    else
      echo "skipping ${p} (feature never used on this host)" >&2
    fi
  done
}
