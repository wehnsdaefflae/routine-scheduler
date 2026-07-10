#!/usr/bin/env sh
# Runs as root: Docker creates any MISSING bind-mount as root:root, which the non-root runtime user
# then can't write (a fresh deploy couldn't even generate its config/token). Make the mount points
# writable by `mark`, then drop privileges and start the daemon.
set -e
for d in \
  "/home/mark/.config/routine-scheduler" \
  "/home/mark/.config/gh" \
  "/home/mark/routines" \
  "/home/mark/.local/share/routine-scheduler-libraries" ; do
  mkdir -p "$d"
  chown mark:mark "$d" 2>/dev/null || true
done
exec gosu mark "$@"
