#!/usr/bin/env bash
# Back up the rsched STATE to another machine as DATED SNAPSHOTS. Safe to run on a schedule.
#
#   deploy/backup.sh [BACKUP_ROOT]        # default: /mnt/sshd_volume1/rsched-backup
#   deploy/backup.sh --help               # the layout, what is kept, and how to restore
#
# Why this exists next to bundle.sh: that one writes a frozen tarball for a ONE-SHOT host move,
# where the source is decommissioned immediately afterwards. This data does not hold still —
# `routines` and `conversations` are rewritten by every run (~1600 files / ~90 MB a day), so a
# tarball is stale within minutes and a nightly re-tar would move gigabytes to capture
# megabytes. rsync moves only the delta, which is what recurring protection needs.
#
# Why dated snapshots and not one mirror: a mirror copies DAMAGE as faithfully as work. A run
# that wrecked state at 01:00 was copied over the last good copy at 03:30, and a deleted
# conversation survived in it only until the next night. So every run writes a folder of its
# own, <root>/snapshots/<YYYY-MM-DD>, and `--link-dest` hard-links each file that did not change
# since the newest earlier snapshot: a snapshot costs only what changed, and every one is still
# a whole copy on its own. `prune_snapshots` keeps 14 daily and 8 weekly. An unchanged file is
# therefore ONE file shared by every snapshot that holds it — never edit inside a snapshot.
#
# It reads the SAME inventory as bundle.sh (deploy/state-paths.sh), so the two cannot drift.
#
# WARNING: the snapshots carry SECRETS — the bearer tokens in config.yaml and the Secrets store
# beside it in ~/.config/routine-scheduler/, the subscription proxy's OAuth logins under its
# cliproxy/auth, the messenger session stores, plus ~/.credentials when that optional
# file-based mechanism is used at all. The root is created mode 700, but a network share may
# not honour that; the script prints the mode it actually got, so read it.
#
# It reads every home as the user it runs as, so a file it may not read fails that home, and
# then the whole night (below). Every container that writes a home writes as that same uid
# (deploy/state-paths.sh); a Permission denied here names a file something wrote as another.
set -euo pipefail

DEFAULT_ROOT=/mnt/sshd_volume1/rsched-backup
LOCK="/tmp/rsched-backup.lock"
KEEP_DAILY=14
KEEP_WEEKLY=8

# shellcheck source=deploy/state-paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/state-paths.sh"

usage() {
  cat <<EOF
usage: deploy/backup.sh [BACKUP_ROOT]        (default: ${DEFAULT_ROOT})

Every run writes BACKUP_ROOT/snapshots/<YYYY-MM-DD> (this host's local date; a second run the
same day refreshes that day's): a complete copy of every data home deploy/state-paths.sh lists,
in which each file unchanged since the newest earlier snapshot is a hard link, not a copy. It is
built under a temporary name and takes its date only once every home copied, so a failed run
leaves no snapshot behind. BACKUP_ROOT/latest points at the newest complete one.
After a successful run it keeps the ${KEEP_DAILY} newest snapshots, plus the newest of each of the
${KEEP_WEEKLY} most recent older ISO weeks that have one, and deletes the rest.

How to restore a home — with the service stopped, so nothing writes it while it is copied back:
  docker compose stop rsched          (host install: systemctl --user stop routine-scheduler)
  rsync -a --delete BACKUP_ROOT/snapshots/<YYYY-MM-DD>/<home>/ ~/<home>/
  docker compose start rsched
<home> is a path as the inventory lists it: routines, conversations, .config/routine-scheduler,
… (for chrome-profile, stop the chrome sidecar too). --delete makes the home exactly what the
snapshot holds, so it also removes what no snapshot carries — .venv, __pycache__, a workspace's
excluded bulk — each of which rebuilds on first use or by its own setup.
One conversation or file is a plain copy:
  cp -a BACKUP_ROOT/snapshots/<YYYY-MM-DD>/conversations/<id> ~/conversations/
A whole host from nothing: rsync -a BACKUP_ROOT/latest/ ~/ — then start it the way a migrated
host starts (deploy/DOCKER.md, step 3).
Never edit a file inside a snapshot: an unchanged file is ONE file every snapshot shares.
EOF
}

# The complete snapshots, oldest first. Only a run that finished gives a folder a date for a
# name, so this is exactly what a run may link against, `latest` may name and retention counts.
snapshot_names() {
  local d
  for d in "${SNAPS}"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]; do
    if [ -d "${d}" ]; then printf '%s\n' "${d##*/}"; fi
  done | LC_ALL=C sort
}

# Re-pointed by RENAMING a fresh link over the old one — never `ln -sf`, which unlinks and then
# creates — so a reader (a restore, a check on the share) never finds `latest` missing.
point_latest_at() {
  ln -sfn "snapshots/$1" "${ROOT}/.latest.new"
  mv -Tf "${ROOT}/.latest.new" "${ROOT}/latest"
}

# MIGRATION(expires=2026-11-15) — the single-mirror layout this root held until now: every home
# directly under it, converged nightly. The first run that finds it MOVES that mirror into
# snapshots/<date of its last completed run> — a rename on the share, instant at any size — so
# this run links against it instead of copying gigabytes again. A mirror that never completed a
# run has no date to give, so it is filed as today's: this run links against it and supersedes
# it. Through `.migrating`, so a cut-short move resumes on the next run instead of stranding half.
migrate_single_mirror() {
  local p top day tops=()
  for p in "${STATE_PATHS_REQUIRED[@]}" "${STATE_PATHS_OPTIONAL[@]}"; do
    top="${p%%/*}"
    if [ -e "${ROOT}/${top}" ] && [[ " ${tops[*]} " != *" ${top} "* ]]; then tops+=("${top}"); fi
  done
  [ "${#tops[@]}" -gt 0 ] || [ -d "${SNAPS}/.migrating" ] || return 0
  day="$(head -c 10 "${ROOT}/.rsched-backup-completed" 2>/dev/null || true)"
  [[ "${day}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || day="${TODAY}"
  mkdir -p "${SNAPS}/.migrating"
  for top in "${tops[@]}"; do mv -T "${ROOT}/${top}" "${SNAPS}/.migrating/${top}"; done
  mv -T "${SNAPS}/.migrating" "${SNAPS}/${day}"
  point_latest_at "${day}"
  echo "migrated: the single mirror in ${ROOT} is now snapshots/${day} (moved, not copied)"
}

# Keep the KEEP_DAILY newest snapshots, plus the newest snapshot of each of the KEEP_WEEKLY most
# recent ISO weeks among the older ones — weeks that HAVE a snapshot, so the nights a host was
# down cost no depth — and delete the rest. Called only once a snapshot completed, and it never
# deletes the one this run wrote, whatever a clock set back makes of the order. Each goes by
# renaming to DISCARD first, so a delete cut short leaves no dated half a restore could pick.
prune_snapshots() {
  local name week seen="" daily=0 weekly=0 pruned=()
  while read -r name; do
    if [ "${daily}" -lt "${KEEP_DAILY}" ]; then daily=$((daily + 1)); continue; fi
    # UTC: a local midnight that daylight saving skips is not a date GNU date will parse
    week="$(date -u -d "${name}" +%G-W%V 2>/dev/null)" || continue   # not a real date: left be
    if [ "${week}" != "${seen}" ]; then
      seen="${week}"
      if [ "${weekly}" -lt "${KEEP_WEEKLY}" ]; then weekly=$((weekly + 1)); continue; fi
    fi
    [ "${name}" != "${TODAY}" ] || continue
    mv -T "${SNAPS}/${name}" "${DISCARD}"
    rm -rf "${DISCARD}"
    pruned+=("${name}")
  done < <(snapshot_names | tac)
  echo "retention: ${daily} daily + ${weekly} weekly kept, ${#pruned[@]} pruned${pruned[*]:+: ${pruned[*]}}"
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  -*)        usage >&2; exit 2 ;;
esac
ROOT="${1:-${DEFAULT_ROOT}}"
# Absolute, because --link-dest reads a relative DIR against the DESTINATION, not the cwd.
case "${ROOT}" in /*) ;; *) ROOT="${PWD}/${ROOT}" ;; esac

# Never let two runs overlap — a scheduled run that is still copying when the next fires would
# have two rsyncs racing to build, rename and prune the same snapshots.
exec 9>"${LOCK}"
flock -n 9 || { echo "another backup is already running (${LOCK}) — nothing to do"; exit 0; }

# THE LOAD-BEARING CHECK. The target is an autofs/sshfs mount of another machine; when it is
# not up, its mountpoint is an ordinary empty directory on the local disk. Writing there would
# fill /home with a copy of /home and report success — a backup that is on the same disk as the
# data, which is the one thing a backup may never be. Touch the path first (a direct autofs
# mount only attaches on access), then require it to live on a DIFFERENT device than $HOME.
mkdir -p "$(dirname "${ROOT}")" 2>/dev/null || true
ls "$(dirname "${ROOT}")" >/dev/null 2>&1 || true

target_dev="$(stat -c %d "$(dirname "${ROOT}")" 2>/dev/null || echo missing)"
home_dev="$(stat -c %d "${HOME}")"
if [ "${target_dev}" = "missing" ]; then
  echo "REFUSING: $(dirname "${ROOT}") does not exist — is the share mounted?" >&2
  exit 1
fi
if [ "${target_dev}" = "${home_dev}" ]; then
  echo "REFUSING: $(dirname "${ROOT}") is on the SAME device as ${HOME} (dev ${home_dev})." >&2
  echo "          The share is not mounted, so this would copy the disk onto itself." >&2
  exit 1
fi

mkdir -p "${ROOT}"
chmod 700 "${ROOT}" 2>/dev/null || true

SNAPS="${ROOT}/snapshots"
WORK="${SNAPS}/.in-progress"   # the snapshot being built — no date, so nothing links to or keeps it
DISCARD="${SNAPS}/.discard"    # a snapshot on its way out
TODAY="$(date +%F)"

rsched_collect_state_paths
migrate_single_mirror
mkdir -p "${SNAPS}"
# What an interrupted run left behind is never a snapshot and never a base to link against.
rm -rf "${WORK}" "${DISCARD}"

RSYNC_EXCLUDES=()
for x in "${STATE_EXCLUDES[@]}"; do RSYNC_EXCLUDES+=("--exclude=${x}"); done

# Unchanged files link to the newest COMPLETE snapshot — on a rerun, this morning's own.
base="$(snapshot_names | tail -1)"
LINK_DEST=()
if [ -n "${base}" ]; then LINK_DEST=("--link-dest=${SNAPS}/${base}"); fi

echo "snapshot ${HOME} → ${SNAPS}/${TODAY}"
echo "  target device ${target_dev} (home is ${home_dev}) — distinct, good"
echo "  mode: $(stat -c %A "${ROOT}")"
if [ -n "${base}" ]; then
  echo "  files unchanged since ${base} are hard-linked to it, not copied"
else
  echo "  no earlier snapshot — this one is a full copy"
fi
echo

mkdir "${WORK}"
started=$(date +%s)
failed=()
for p in "${STATE_PATHS[@]}"; do
  printf '  %-42s ' "${p}"
  # --relative from the `/./` in the source: rsync sees every file under its HOME-relative name
  # (`git-repos/LLMSecTest_agentic/apps/…`) — the name bundle.sh's tar sees — and recreates the
  # home at that path in the snapshot. That is what makes STATE_EXCLUDES mean the same thing to
  # both consumers. Synced from INSIDE each home, rsync saw only `apps/…`, so every exclude
  # anchored to a workspace matched nothing and the copy carried the bulk the tarball leaves out.
  # No --delete: every snapshot starts EMPTY, so a file an exclude names never reaches it — not
  # even one an older snapshot still holds from before the exclude existed. The single mirror
  # needed --delete-excluded for that (rsync protects excluded files on the receiver, which is how
  # a stale Chrome SingletonLock survived being excluded); a fresh folder makes it structural.
  # Owner and group are dropped: we are not root, and a NAS export usually cannot represent them
  # anyway — and --link-dest links a file only when every PRESERVED attribute matches, so they
  # could only cost links.
  # --one-file-system is LOAD-BEARING, not tidiness: a routine that binds a remote machine gets
  # its share sshfs-mounted at <routine>/mnt/<name> while it runs (docs/remote-machines.md), and
  # without -x a backup firing at that moment would descend the mount and copy another machine's
  # filesystem into the snapshot. Every state home is on one device, so -x is otherwise invisible.
  # Never --inplace: an update must write a NEW file, because the old one is shared with every
  # earlier snapshot that linked it, and rewriting it in place would rewrite their past.
  # --omit-link-times because the default share is sshfs, where a symlink's times cannot be set:
  # SFTP sets a path's times by following it on the NAS, and rsync failed every such attempt
  # with "failed to set times on <link>: No such file or directory" — exit 23. The two homes
  # holding a symlink (conversations, the LLMSecTest workspace) failed that way night after
  # night, and with them the whole snapshot. The link is copied either way; only its mtime is
  # not, which nothing reads — and since link times are then not compared either, an unchanged
  # link is still hard-linked to the night before.
  rc=0
  out=$(rsync -a -R -x --no-owner --no-group --omit-link-times --stats "${LINK_DEST[@]}" \
             "${RSYNC_EXCLUDES[@]}" "${HOME}/./${p}/" "${WORK}/" 2>&1) || rc=$?
  # 24 is "some files vanished before they could be transferred": a file deleted between rsync's
  # listing and its copy — an atomic write's tmp, a LevelDB compaction in chrome-profile. It is
  # the one non-zero exit that still leaves a complete copy of everything that existed throughout,
  # which is all a snapshot of a live instance can be; failing on it would cost the whole night.
  if [ "${rc}" -eq 0 ] || [ "${rc}" -eq 24 ]; then
    xfer=$(echo "${out}" | awk -F': *' '/Number of regular files transferred/ {print $2}')
    sent=$(echo "${out}" | awk -F': *' '/Total transferred file size/ {print $2}')
    vanished=""
    if [ "${rc}" -eq 24 ]; then
      vanished=", $(echo "${out}" | grep -c '^file has vanished' || true) vanished mid-copy"
    fi
    echo "ok  (${xfer:-0} files, ${sent:-0}${vanished})"
  else
    echo "FAILED"
    failed+=("${p}")
    # rsync's ERRORS, not its closing stats block — a tail here shows the byte counts and hides
    # the reason, which is the one thing the operator needs. Printed by THIS shell, never by the
    # pipeline that finds them: journald files a line under its unit by the writer's cgroup, and
    # a pipeline's last process has exited before journald looks — so `journalctl -u
    # rsched-backup` showed every FAILED and not one reason (2026-10-01).
    reasons=$(echo "${out}" | grep -E '^rsync|^IO error|cannot ' | head -8 || true)
    while IFS= read -r line; do
      if [ -n "${line}" ]; then printf '      %s\n' "${line}"; fi
    done <<<"${reasons}"
  fi
done

elapsed=$(( $(date +%s) - started ))
echo

if [ ${#failed[@]} -gt 0 ]; then
  rm -rf "${WORK}"
  echo "INCOMPLETE — these homes did not copy: ${failed[*]}" >&2
  echo "             no snapshot was kept and latest is unchanged   elapsed: ${elapsed}s" >&2
  exit 1
fi

# A live instance is not quiescent: chrome-profile is a LevelDB store the sidecar rewrites
# continuously, so its copy may be torn and restore as a signed-out browser. Nothing else here
# is written in a way a torn copy breaks — routine repos autocommit, and run logs are
# append-only JSONL. Say so rather than implying the snapshot is consistent.
if [ -e "${HOME}/chrome-profile" ]; then
  echo "note: chrome-profile was copied live and may be torn. For a consistent copy of the"
  echo "      browser sessions (a rerun refreshes today's snapshot):"
  echo "      docker compose stop chrome && deploy/backup.sh && docker compose start chrome"
  echo
fi

# Complete — only now does the folder take a date for a name. On a rerun the same day the
# earlier snapshot steps aside first (a directory cannot be renamed over a non-empty one) and
# is deleted once the new one holds the name.
if [ -e "${SNAPS}/${TODAY}" ]; then mv -T "${SNAPS}/${TODAY}" "${DISCARD}"; fi
mv -T "${WORK}" "${SNAPS}/${TODAY}"
point_latest_at "${TODAY}"
date -Iseconds > "${ROOT}/.rsched-backup-completed"
rm -rf "${DISCARD}"

# What the snapshot cost the share: du counts a hard-linked file once per invocation, so the
# second figure is only what the previous snapshot does not already hold. Were the share unable
# to hard-link, rsync would quietly copy instead — and this is the line that would show it.
prev=""
while read -r name; do if [[ "${name}" < "${TODAY}" ]]; then prev="${name}"; fi; done \
  < <(snapshot_names)
if [ -n "${prev}" ]; then
  added="$(du -sh "${SNAPS}/${prev}" "${SNAPS}/${TODAY}" 2>/dev/null | tail -1 | cut -f1 || true)"
  echo "snapshot ${TODAY}: ${added:-?} new on the share, the rest shared with ${prev}   elapsed: ${elapsed}s"
else
  size="$(du -sh "${SNAPS}/${TODAY}" 2>/dev/null | cut -f1 || true)"
  echo "snapshot ${TODAY}: ${size:-?}, a full copy   elapsed: ${elapsed}s"
fi

prune_snapshots
echo "done."
