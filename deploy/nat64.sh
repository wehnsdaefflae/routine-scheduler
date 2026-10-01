#!/usr/bin/env bash
# Turn the NAT64 resolver override on or off for the fleet. See compose.nat64.yml for what it is.
#
# `--profile claude-proxy` is not optional: cliproxy sits behind that profile, and a compose run
# without it cannot settle the project's networks — it stops the other three and leaves the model
# transport stranded. Learned the hard way on 2026-09-27.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  on)  echo "NAT64 on — the fleet will reach IPv4-only hosts through a public gateway"
       docker compose -f docker-compose.yml -f compose.nat64.yml --profile claude-proxy up -d ;;
  off) echo "NAT64 off — back to this line's own routing"
       docker compose --profile claude-proxy up -d --remove-orphans ;;
  status)
       for c in rsched rsched-chrome; do
         printf '  %-14s dns=%s\n' "$c" "$(docker inspect "$c" --format '{{if .HostConfig.Dns}}{{.HostConfig.Dns}}{{else}}(default){{end}}' 2>/dev/null || echo '-')"
       done ;;
  *)   echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac
