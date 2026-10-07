#!/usr/bin/env bash
# desktop-mount mount|unmount TAG NAME — the ONE root action the desktop user may take (sudoers
# names this file and nothing else). A folder the broker attached as virtio-fs device TAG appears
# at ~/mnt/NAME. Both words are checked against a strict shape, so this cannot be talked into
# mounting anything but a virtio-fs tag onto a directory under ~/mnt.
set -euo pipefail
op="${1:-}"; tag="${2:-}"; name="${3:-}"
shape='^[a-z0-9][a-z0-9._-]{0,47}$'
[[ "$tag" =~ $shape ]] || { echo "bad tag" >&2; exit 2; }
[[ "$name" =~ $shape ]] || { echo "bad name" >&2; exit 2; }
target="/home/mark/mnt/$name"
case "$op" in
    mount)
        install -d -o mark -g mark /home/mark/mnt "$target"
        mountpoint -q "$target" || mount -t virtiofs "$tag" "$target"
        ;;
    unmount)
        if mountpoint -q "$target"; then umount "$target" || umount -l "$target"; fi
        rmdir "$target" 2>/dev/null || true
        ;;
    *) echo "usage: desktop-mount mount|unmount TAG NAME" >&2; exit 2 ;;
esac
