#!/usr/bin/env bash
# Mirror the rsched STATE to another machine, incrementally. Safe to run on a schedule.
#
#   deploy/backup.sh [MIRROR_ROOT]        # default: /mnt/sshd_volume1/rsched-backup
#
# Why this exists next to bundle.sh: that one writes a frozen tarball for a ONE-SHOT host move,
# where the source is decommissioned immediately afterwards. This data does not hold still —
# `routines` and `conversations` are rewritten by every run (~1600 files / ~90 MB a day), so a
# snapshot is stale within minutes and a nightly re-tar would move gigabytes to capture
# megabytes. rsync moves the delta and converges, which is what recurring protection needs.
#
# It reads the SAME inventory as bundle.sh (deploy/state-paths.sh), so the two cannot drift.
#
# WARNING: the mirror carries SECRETS — the bearer tokens and the Secrets store in
# ~/.config/routine-scheduler/config.yaml, plus ~/.credentials when that optional file-based
# mechanism is used at all. The mirror root is created mode 700, but a network share may not
# honour that; the script prints the mode it actually got, so read it.
set -euo pipefail

MIRROR="${1:-/mnt/sshd_volume1/rsched-backup}"
LOCK="/tmp/rsched-backup.lock"

# shellcheck source=deploy/state-paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/state-paths.sh"

# Never let two runs overlap — a scheduled mirror that is still running when the next fires
# would have two rsyncs racing on the same tree, and --delete makes that destructive.
exec 9>"${LOCK}"
flock -n 9 || { echo "another backup is already running (${LOCK}) — nothing to do"; exit 0; }

# THE LOAD-BEARING CHECK. The target is an autofs/sshfs mount of another machine; when it is
# not up, its mountpoint is an ordinary empty directory on the local disk. Writing there would
# fill /home with a copy of /home and report success — a backup that is on the same disk as the
# data, which is the one thing a backup may never be. Touch the path first (a direct autofs
# mount only attaches on access), then require it to live on a DIFFERENT device than $HOME.
mkdir -p "$(dirname "${MIRROR}")" 2>/dev/null || true
ls "$(dirname "${MIRROR}")" >/dev/null 2>&1 || true

target_dev="$(stat -c %d "$(dirname "${MIRROR}")" 2>/dev/null || echo missing)"
home_dev="$(stat -c %d "${HOME}")"
if [ "${target_dev}" = "missing" ]; then
  echo "REFUSING: $(dirname "${MIRROR}") does not exist — is the share mounted?" >&2
  exit 1
fi
if [ "${target_dev}" = "${home_dev}" ]; then
  echo "REFUSING: $(dirname "${MIRROR}") is on the SAME device as ${HOME} (dev ${home_dev})." >&2
  echo "          The share is not mounted, so this would copy the disk onto itself." >&2
  exit 1
fi

mkdir -p "${MIRROR}"
chmod 700 "${MIRROR}" 2>/dev/null || true

rsched_collect_state_paths

RSYNC_EXCLUDES=()
for x in "${STATE_EXCLUDES[@]}"; do RSYNC_EXCLUDES+=("--exclude=${x}"); done

echo "mirroring ${HOME} → ${MIRROR}"
echo "  target device ${target_dev} (home is ${home_dev}) — distinct, good"
echo "  mode: $(stat -c %A "${MIRROR}")"
echo

started=$(date +%s)
failed=()
for p in "${STATE_PATHS[@]}"; do
  dest="${MIRROR}/${p}"
  mkdir -p "$(dirname "${dest}")"
  printf '  %-42s ' "${p}"
  # --delete so the mirror CONVERGES rather than accumulating deleted files forever. Owner and
  # group are dropped: we are not root, and a NAS export usually cannot represent them anyway.
  # --one-file-system is LOAD-BEARING, not tidiness: a routine that binds a remote machine gets
  # its share sshfs-mounted at <routine>/mnt/<name> while it runs (docs/remote-machines.md), and
  # without -x a backup firing at that moment would descend the mount and copy another machine's
  # filesystem into the mirror. Every state home is on one device, so -x is otherwise invisible.
  # --delete-excluded, not plain --delete: rsync PROTECTS excluded files on the receiver, so
  # anything the exclude list gains later — or that a pre-exclusion run already copied — would
  # sit in the mirror forever. That is how a stale Chrome SingletonLock survived being excluded.
  if out=$(rsync -a -x --delete --delete-excluded --no-owner --no-group --stats \
             "${RSYNC_EXCLUDES[@]}" \
             "${HOME}/${p}/" "${dest}/" 2>&1); then
    xfer=$(echo "${out}" | awk -F': *' '/Number of regular files transferred/ {print $2}')
    sent=$(echo "${out}" | awk -F': *' '/Total transferred file size/ {print $2}')
    echo "ok  (${xfer:-0} files, ${sent:-0})"
  else
    echo "FAILED"
    failed+=("${p}")
    # rsync's ERRORS, not its closing stats block — a tail here shows the byte counts and hides
    # the reason, which is the one thing the operator needs.
    echo "${out}" | grep -E '^rsync|^IO error|cannot ' | head -8 | sed 's/^/      /'
  fi
done

elapsed=$(( $(date +%s) - started ))
echo
echo "mirror size: $(du -sh "${MIRROR}" 2>/dev/null | cut -f1)   elapsed: ${elapsed}s"

if [ ${#failed[@]} -gt 0 ]; then
  echo "INCOMPLETE — these homes did not mirror: ${failed[*]}" >&2
  exit 1
fi

# A live instance is not quiescent: chrome-profile is a LevelDB store the sidecar rewrites
# continuously, so its mirrored copy may be torn and restore as a signed-out browser. Nothing
# else here is written in a way a torn copy breaks — routine repos autocommit, and run logs are
# append-only JSONL. Say so rather than implying the mirror is a consistent snapshot.
if [ -e "${HOME}/chrome-profile" ]; then
  echo
  echo "note: chrome-profile was copied live and may be torn. For a consistent copy of the"
  echo "      browser sessions: docker compose stop chrome && deploy/backup.sh && docker compose start chrome"
fi

date -Iseconds > "${MIRROR}/.rsched-backup-completed"
echo "done."
