#!/usr/bin/env bash
# Turn the NAT64 resolver override on or off for the fleet. See compose.nat64.yml for what it is.
#
# The switch is a line in `.env`, never a command-line flag. Compose reads COMPOSE_FILE and
# COMPOSE_PROFILES from `.env` on every command run in the checkout's root, so the file set this
# script picks is the one every later `build`, `up -d`, `exec` and `logs` uses as well
# (deploy/DOCKER.md, "One compose selection per host"). A file list replaces Compose's default —
# and with it the auto-loading of the host's docker-compose.override.yml — so the list names the
# override itself whenever the file exists. The first version of this script handed its two
# files to its own `up` instead. The override was never loaded: every container ran without its
# memory ceiling from 2026-09-27 until the 0.372.1 deploy.
#
# `off` deletes the line rather than writing the shorter list, so Compose is back on its own
# default — docker-compose.yml, plus the override whenever it exists — and nothing can go stale.
# While NAT64 is on, the list is fixed when it is written: run `on` again after creating or
# deleting the override.
#
# The profiles are the host's, not this script's: a host that runs the subscription proxy keeps
# COMPOSE_PROFILES=claude-proxy in `.env` (docs/claude-proxy-cutover.md). They are checked rather
# than assumed, because an `up` whose selection leaves out a service that has a container here
# cannot settle the project's networks — it stops the rest and strands that one, which on
# 2026-09-27 was the model transport. A refusal leaves `.env` exactly as it was.
set -euo pipefail
cd "$(dirname "$0")/.."

# The daemon a compose command reaches is whatever the calling shell sees, and nothing here tied
# that to the checkout these files come from: run on omen-laptop inside the sshfs mount of the
# server's checkout, `off` rewrote the SERVER's .env and then built the fleet on the LAPTOP,
# whose empty container list passed the left_out check below. Refuse before `switch` writes .env
# and before `status` reports a fleet that is not this host's. See deploy/docker-host-guard.sh.
. "$(dirname "$0")/docker-host-guard.sh"

MARK='# deploy/nat64.sh: NAT64 is on. Every compose command run here reads this file set; deploy/nat64.sh off removes it.'
NEW=""      # the copy put_env is writing; removed if the script dies before the rename
trap '[ -z "$NEW" ] || rm -f "$NEW"' EXIT

usage() { echo "usage: $0 on|off|status" >&2; exit 2; }

# Replace .env with $1 in one rename. mktemp creates the copy 0600, which .env must stay: it
# carries BROWSER_CDP_TOKEN.
put_env() {
  NEW=$(mktemp .env.XXXXXX)
  printf '%s' "$1" > "$NEW"
  mv -f "$NEW" .env
  NEW=""
}

# .env without the two lines this script owns, every other line as written.
others() { awk -v mark="$MARK" '$0 != mark && !/^COMPOSE_FILE=/' .env; }

# The value .env gives $1 — what Compose reads, since nothing in the shell overrides it (below).
dotenv() { sed -n "s/^$1=//p" .env | tail -n 1; }

# Compose's own file set, used when nothing names one.
default_files() {
  printf 'docker-compose.yml'
  if [ -f docker-compose.override.yml ]; then printf ':docker-compose.override.yml'; fi
}

# Write COMPOSE_FILE=$1 into .env (no line at all when $1 is empty), then bring the fleet onto
# it; the remaining arguments are what to say once that is known to be safe.
switch() {
  local before after have chosen left_out
  # BEFORE put_env: a refusal must leave .env exactly as it was, which is this script's own
  # promise and the thing the wrong-host run broke (it rewrote the server's .env from a laptop).
  require_local_docker_host "${ACTION:-on|off}" || exit 4
  before=$(cat .env; printf x); before=${before%x}
  after=$(others; printf x); after=${after%x}
  if [ -n "$1" ]; then after+="$MARK"$'\n'"COMPOSE_FILE=$1"$'\n'; fi
  put_env "$after"
  if ! have=$(docker compose ps -a --format '{{.Service}}') \
     || ! chosen=$(docker compose config --services); then
    put_env "$before"
    exit 1
  fi
  left_out=$(LC_ALL=C comm -23 <(LC_ALL=C sort -u <<<"$have") <(LC_ALL=C sort -u <<<"$chosen") |
             paste -sd ' ')
  if [ -n "$left_out" ]; then
    put_env "$before"
    echo "refusing, .env unchanged: $left_out has a container here, but the selection leaves it out." >&2
    echo "Name its profile in .env first: COMPOSE_PROFILES=<the profile docker-compose.yml gives it>." >&2
    exit 3
  fi
  shift
  printf '%s\n' "$@"
  docker compose up -d
}

# The selection, then each container: its resolvers, its memory ceiling, and whether its config
# is the one the selection gives its service (Compose's own recreate test, the config hash).
status() {
  local files profiles hashes name svc dns mem hash want verdict
  # Run from the laptop this listed no containers and reported nothing wrong, which reads as a
  # healthy fleet. A report about the wrong daemon is worse than no report.
  require_local_docker_host status || exit 4
  files=$(dotenv COMPOSE_FILE)
  profiles=$(dotenv COMPOSE_PROFILES)
  if [ -n "$files" ]; then
    printf '  %-30s %s   (NAT64 on)\n' "COMPOSE_FILE" "$files"
  else
    printf "  %-30s %s   (no line: Compose's default, NAT64 off)\n" "COMPOSE_FILE" "$(default_files)"
  fi
  printf '  %-30s %s\n' "COMPOSE_PROFILES" "${profiles:-(none)}"
  hashes=$(docker compose config --hash '*')
  docker compose ps -a --format '{{.Name}}' | sort | while read -r name; do
    IFS='|' read -r svc dns mem hash < <(docker inspect "$name" --format \
      '{{index .Config.Labels "com.docker.compose.service"}}|{{if .HostConfig.Dns}}{{.HostConfig.Dns}}{{else}}(default){{end}}|{{.HostConfig.Memory}}|{{index .Config.Labels "com.docker.compose.config-hash"}}')
    want=$(awk -v s="$svc" '$1 == s { print $2 }' <<<"$hashes")
    if [ -z "$want" ]; then verdict="outside the selection: name its profile in COMPOSE_PROFILES"
    elif [ "$want" = "$hash" ]; then verdict="config matches the selection"
    else verdict="config differs: the next \`docker compose up -d\` recreates it"
    fi
    if [ "$mem" = 0 ]; then mem=none; else mem=$(numfmt --to=iec "$mem"); fi
    printf '  %-30s dns=%s  mem=%s  %s\n' "$name" "$dns" "$mem" "$verdict"
  done
}

# Exported in the shell, either variable beats .env — the line below would then govern nothing.
for var in COMPOSE_FILE COMPOSE_PROFILES; do
  if printenv "$var" > /dev/null; then
    echo "$var is set in this shell, which beats .env: unset it, then run this again" >&2
    exit 2
  fi
done
[ -f .env ] || { echo "no .env in $PWD: create it first (deploy/DOCKER.md, step 1)" >&2; exit 2; }

ACTION="${1:-}"      # what the refusal tells him to re-run on the right host
case "${1:-}" in
  on)  files="$(default_files):compose.nat64.yml"
       switch "$files" "NAT64 on — the fleet will reach IPv4-only hosts through a public gateway" \
                       "  .env: COMPOSE_FILE=$files" ;;
  off) switch "" "NAT64 off — back to this line's own routing" \
                 "  .env: no COMPOSE_FILE, so Compose loads $(default_files | tr ':' ' ')" ;;
  status) status ;;
  *)   usage ;;
esac
