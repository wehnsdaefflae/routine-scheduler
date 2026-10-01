#!/usr/bin/env bash
# Refuse a compose command whose Docker daemon is not on the host that holds this checkout.
#
# The defect (2026-10-01): `deploy/nat64.sh off` was run on omen-laptop inside the sshfs mount of
# the SERVER's checkout (/home/mark/server/home/mark/git-repos/routine-scheduler). The script
# rewrote the server's `.env` through the mount, then talked to the LAPTOP's Docker Desktop. The
# laptop had no containers, so nat64.sh's only safety check — "does the selection leave out a
# service that has a container here?" — passed trivially on an empty list. `docker compose up -d`
# then built rsched, rsched-tor and rsched-chrome on the laptop (~5.2 GB), pulled cliproxy, and
# created the routine-scheduler_browser network there. It stopped only because the default
# network's address pool happened to overlap a laptop network; with a free subnet the laptop would
# have started a SECOND scheduler daemon bound to its stale July copy of ~/routines. Meanwhile the
# server stayed on NAT64 while the script printed "NAT64 off".
#
# Every deploy script that drives compose has this shape, because the daemon a compose command
# reaches is whatever the calling shell sees, and nothing ties that to the checkout the script
# reads its files from. Two signals catch it, both verified on both hosts on 2026-10-01:
#
#   * `docker info --format '{{.Name}}'` is the daemon's own host name: `ubuntuserver` on the
#     server (== `hostname`), `docker-desktop` against omen-laptop. A remote DOCKER_HOST reports
#     the remote host's name too, which is the same fault with a different cause.
#   * `findmnt -T . -no FSTYPE` of fuse.sshfs / nfs / cifs means the checkout lives on another
#     machine, so every bind path in docker-compose.yml resolves on the wrong one.
#
# Either one is a refusal, and the message names the host to run the command on — the operator's
# actual next step, which "wrong host" alone does not give him.
#
# Usage, from a script in this directory:
#     . "$(dirname "$0")/docker-host-guard.sh"
#     require_local_docker_host            # before the FIRST docker command, and before any
#                                          # write that a wrong-host run would misdirect
#
# `status`-style read-only commands call it too: run from the laptop, nat64.sh's status listed no
# containers and reported nothing wrong, which reads as a healthy fleet.

# The checkout this script belongs to (not $PWD: a caller may cd anywhere).
_guard_checkout() { cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd; }

# "" when the checkout is on a local filesystem, else the network filesystem's type.
_guard_network_fs() {
  local fs
  fs=$(findmnt -T "$(_guard_checkout)" -no FSTYPE 2>/dev/null) || return 0
  case "$fs" in
    fuse.sshfs | sshfs | nfs | nfs4 | cifs | smb3 | fuse.rclone | afs) printf '%s' "$fs" ;;
  esac
}

# Refuse unless the Docker daemon runs on the host holding this checkout. Prints what it
# compared, so a refusal is diagnosable without re-running the two commands by hand.
require_local_docker_host() {
  local checkout netfs daemon here
  checkout=$(_guard_checkout)

  # 1. The checkout itself. Checked FIRST: it needs no daemon, and it is the signal that says
  #    "the files you are about to rewrite belong to another machine".
  netfs=$(_guard_network_fs)
  if [ -n "$netfs" ]; then
    cat >&2 <<EOF
refusing: this checkout is on a $netfs mount, so it belongs to another host.
  checkout: $checkout
Compose would resolve every bind path in docker-compose.yml on THIS machine, against files that
live on the other one, and a write to .env would change the other host's configuration.
Run the command on the host that owns the checkout, e.g.
  ssh <that host> 'cd <the real checkout path> && $(basename -- "$0") ${1:-<args>}'
EOF
    return 1
  fi

  # 2. The daemon. `docker info` needs a reachable daemon; a failure here is not this guard's
  #    business beyond saying so, since every later docker command would fail the same way.
  if ! daemon=$(docker info --format '{{.Name}}' 2>/dev/null) || [ -z "$daemon" ]; then
    echo "refusing: no Docker daemon answered \`docker info\` (is it running? is DOCKER_HOST set to a host that is down?)" >&2
    return 1
  fi
  here=$(hostname)
  if [ "$daemon" != "$here" ]; then
    cat >&2 <<EOF
refusing: the Docker daemon is not on this host.
  docker info Name: $daemon
  hostname:         $here
  DOCKER_HOST:      ${DOCKER_HOST:-(unset)}
  checkout:         $checkout
Compose would build images, create networks and start containers on '$daemon' from files that
live here — a second fleet on the wrong machine, bound to whatever that machine has at these
paths. Run the command on '$daemon' itself, or unset DOCKER_HOST so it reaches this host's
daemon.
EOF
    return 1
  fi
  return 0
}
