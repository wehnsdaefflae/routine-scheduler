#!/usr/bin/env bash
# The X session the agent drives: Xvnc (an X server that IS a VNC server — no GPU, no display
# device, no window on any host) and XFCE inside one D-Bus session, with the control agent in
# that same session so it reaches the accessibility bus. Run by desktop.service as the desktop
# user; when anything here exits, systemd starts the whole session again.
set -euo pipefail

GEOMETRY="${DESKTOP_GEOMETRY:-1280x800}"
export DISPLAY=:1

# Xvnc listens on the guest's private link; the sidecar container is the only thing that can
# route there, and it puts the bearer-token proxy and noVNC in front. -SecurityTypes None is
# therefore not an open door — the door is the container's.
Xvnc :1 -geometry "$GEOMETRY" -depth 24 -SecurityTypes None -rfbport 5900 -localhost=0 \
    -AlwaysShared -AcceptSetDesktopSize=0 -nolisten tcp -desktop "rsched desktop" &
for _ in $(seq 1 100); do
    [ -S /tmp/.X11-unix/X1 ] && break
    sleep 0.1
done
xset s off -dpms s noblank 2>/dev/null || true

exec dbus-run-session -- /usr/local/bin/desktop-inner
