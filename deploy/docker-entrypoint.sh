#!/usr/bin/env sh
# Runs as root: Docker creates any MISSING bind-mount as root:root, which the non-root runtime user
# then can't write (a fresh deploy couldn't even generate its config/token). Make the mount points
# writable by `mark`, then drop privileges and start the daemon.
set -e
for d in \
  "/home/mark/.config/routine-scheduler" \
  "/home/mark/.config/gh" \
  "/home/mark/routines" \
  "/home/mark/.local/share/routine-scheduler-libraries" \
  "/home/mark/.local/state" \
  "/home/mark/.cache/ms-playwright" \
  "/home/mark/.cache/uv" ; do
  mkdir -p "$d"
  chown mark:mark "$d" 2>/dev/null || true
done
# ~/.cache/uv needs a RECURSIVE repair, not just its top dir: uv creates per-script
# environments under it at every util call, and anything that runs uv in this container as
# root (a `docker exec` without -u, which is what root-by-default gives you) leaves
# environments-v2/ — or a wheels-v6/ entry — owned by root INSIDE an otherwise mark-owned
# tree. The uid-1000 daemon then fails to create its env and EVERY util call in the instance
# dies with "failed to create directory .../environments-v2/...: Permission denied", for every
# routine at once (2026-08-26; same class as the F97 ~/.local/state bug above and the
# util-stats root:root one). Only the offenders are touched, so this stays cheap on a warm
# cache.
find /home/mark/.cache/uv ! -user mark -exec chown mark:mark {} + 2>/dev/null || true
# NOTE ~/.local/state above: the util-stats snapshot (util_stats.snapshot_path(), the Stats
# tab + `util-stats` util's single source of truth) is written to ~/.local/state/routine-
# scheduler/. That dir is NOT a bind mount, so Docker created ONLY the sibling bind parent
# ~/.local/share as root — leaving ~/.local and ~/.local/state root-owned, so the uid-1000
# daemon's mkdir of ~/.local/state/routine-scheduler failed with PermissionError, silently,
# for four releases (F97). Chowning the XDG state ROOT to mark lets the daemon create any
# state subdir it needs, now and for future consumers.
# The gh device-flow token (mounted ~/.config/gh) is useless to git without the credential
# helper glue — without it, every in-container push fails "could not read Username" while
# host pushes work, and self-audit's commits strand locally.
gosu mark sh -c 'command -v gh >/dev/null && gh auth status >/dev/null 2>&1 && gh auth setup-git 2>/dev/null' || true

exec gosu mark "$@"
