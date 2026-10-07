#!/usr/bin/env bash
# Inside the session bus: accessibility switched on for every toolkit, the control agent kept
# alive beside the desktop, then XFCE in the foreground.
set -uo pipefail

# Each toolkit has its own switch for "expose yourself to AT-SPI even with no screen reader
# running": GTK's bridge, Firefox (reads GNOME_ACCESSIBILITY), Qt, and Chromium/Electron.
export GTK_MODULES=gail:atk-bridge NO_AT_BRIDGE=0 GNOME_ACCESSIBILITY=1 QT_ACCESSIBILITY=1 \
       QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 ACCESSIBILITY_ENABLED=1
# Start the accessibility bus now rather than on the first client's demand, so the first
# listing does not race its launch.
/usr/libexec/at-spi-bus-launcher --launch-immediately &

(
    cd /usr/local/lib/desktop-agent
    while true; do
        python3 agent.py || true
        sleep 1
    done
) &

exec startxfce4
