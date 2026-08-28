#!/usr/bin/env bash
# Bring up the virtual display, the hands-on VNC path, and Chrome — in that order, because each
# needs the one before it. Chrome runs in the FOREGROUND as PID 1's child: when the browser dies
# the container exits and compose's restart policy brings it back with the profile intact.
#
# Runs as root only long enough to make the bind-mounted profile writable (Docker creates a
# missing bind root-owned), then drops to `mark` — the same dance the engine entrypoint does.
set -euo pipefail

log() { printf '[chrome] %s\n' "$*" >&2; }

PROFILE="${CHROME_PROFILE:-/home/mark/chrome-profile}"
DISPLAY_NUM="${DISPLAY:-:99}"
GEOMETRY="${SCREEN_GEOMETRY:-1920x1080x24}"
CDP_PORT="${CDP_PORT:-9222}"
VNC_PORT="${VNC_PORT:-6080}"
# "1920x1080x24" (an Xvfb screen spec) -> "1920,1080" (what Chrome's --window-size wants)
WINDOW_SIZE="$(printf '%s' "$GEOMETRY" | cut -d x -f1,2 | tr x ,)"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$PROFILE"
    chown -R mark:mark "$PROFILE" /home/mark
    log "profile $PROFILE ready; dropping to mark"
    exec gosu mark "$0" "$@"
fi

# A profile copied off a running browser, or left behind by a container that was killed rather
# than stopped, keeps these lock files. Chrome then refuses to use the directory and silently
# opens a throwaway profile instead — which looks exactly like "all my logins are gone".
rm -f "$PROFILE"/Singleton* 2>/dev/null || true

log "starting Xvfb on $DISPLAY_NUM ($GEOMETRY)"
Xvfb "$DISPLAY_NUM" -screen 0 "$GEOMETRY" -nolisten tcp &
for _ in $(seq 1 50); do
    xdpyinfo -display "$DISPLAY_NUM" >/dev/null 2>&1 && break
    sleep 0.2
done

# x11vnc listens on loopback only. The way in is the noVNC page below, whose port compose
# publishes to the HOST's loopback — so reaching the browser means an SSH tunnel or a tailnet
# proxy, never an open VNC port on the LAN.
log "starting x11vnc (loopback :5900)"
x11vnc -display "$DISPLAY_NUM" -rfbport 5900 -localhost -forever -shared -nopw -quiet &

log "starting noVNC on :$VNC_PORT"
websockify --web=/usr/share/novnc "$VNC_PORT" 127.0.0.1:5900 &

# --password-store=basic: there is no keyring in a container. Left to guess, Chrome picks a
# backend per desktop-environment heuristics and can wrap the cookie key in something that is
# not here, which reads as "logged out" every start. `basic` is deterministic and portable —
# it is also what makes a profile seeded from another machine openable at all.
# The window is sized to the whole framebuffer so the noVNC view is a full browser rather than a
# small window on a large black desktop — there is no window manager here to maximize it.
# dbus-launch gives Chrome the session bus it expects; without one every start buries the real
# log lines under a screenful of "Failed to connect to the bus".
log "starting Chrome (headful under Xvfb, CDP on 127.0.0.1:$CDP_PORT)"
exec dbus-launch --exit-with-session google-chrome-stable \
    --user-data-dir="$PROFILE" \
    --remote-debugging-port="$CDP_PORT" \
    --remote-allow-origins=* \
    --password-store=basic \
    --window-position=0,0 \
    --window-size="$WINDOW_SIZE" \
    --disable-gpu \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    "$@"
