#!/usr/bin/env bash
# First boot of a fresh home disk: give it the skeleton (XFCE's panel layout, so the desktop
# does not open on the "first start of the panel" question). The home disk persists across
# container restarts and image upgrades; the system disk is rebuilt from the image every start.
set -euo pipefail
HOME_DIR=/home/mark
if [ ! -e "$HOME_DIR/.desktop-initialized" ]; then
    cp -a /etc/skel/. "$HOME_DIR"/
    chown -R mark:mark "$HOME_DIR"
    touch "$HOME_DIR/.desktop-initialized"
fi
