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
CDP_INTERNAL_PORT="${CDP_INTERNAL_PORT:-9223}"
VNC_PORT="${VNC_PORT:-6080}"
VNC_INTERNAL_PORT="${VNC_INTERNAL_PORT:-6081}"

# Both protocols this container speaks authenticate NOTHING of their own, and the engine
# container is one docker network away — so the ports are fronted by a bearer-token proxy and
# the real services listen on loopback only. Refuse to start without the token rather than open
# an unauthenticated door that looks guarded: a browser holding live site sessions is the most
# valuable thing on this host.
if [ -z "${BROWSER_CDP_TOKEN:-}" ]; then
    log "FATAL: BROWSER_CDP_TOKEN is not set. The CDP and noVNC ports have no authentication"
    log "       of their own, so this container will not start an unauthenticated browser."
    log "       Set it in the compose environment (see deploy/DOCKER.md)."
    exit 78    # EX_CONFIG
fi
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

log "starting noVNC on 127.0.0.1:$VNC_INTERNAL_PORT (fronted by the auth proxy)"
websockify --web=/usr/share/novnc "127.0.0.1:$VNC_INTERNAL_PORT" 127.0.0.1:5900 &

# Chrome refuses to bind DevTools anywhere but loopback, so this is how another container reaches
# it — now through the auth proxy rather than a bare socat forward. The proxy keeps the property
# that made the forward work: it listens on the address the client dialled and passes `Host`
# through untouched, so DevTools still accepts the IP literal and echoes it back in the
# websocket URL it hands out. Nothing has to rewrite those URLs.
log "fronting CDP :$CDP_PORT -> 127.0.0.1:$CDP_INTERNAL_PORT and noVNC :$VNC_PORT -> 127.0.0.1:$VNC_INTERNAL_PORT"
python3 /usr/local/bin/browser-auth-proxy.py \
    --map "0.0.0.0:${CDP_PORT}=127.0.0.1:${CDP_INTERNAL_PORT}:cdp" \
    --map "0.0.0.0:${VNC_PORT}=127.0.0.1:${VNC_INTERNAL_PORT}:novnc" &

# --password-store=basic: there is no keyring in a container. Left to guess, Chrome picks a
# backend per desktop-environment heuristics and can wrap the cookie key in something that is
# not here, which reads as "logged out" every start. `basic` is deterministic and portable —
# it is also what makes a profile seeded from another machine openable at all.
# The window is sized to the whole framebuffer so the noVNC view is a full browser rather than a
# small window on a large black desktop — there is no window manager here to maximize it.
# dbus-launch gives Chrome the session bus it expects; without one every start buries the real
# log lines under a screenful of "Failed to connect to the bus".
log "starting Chrome (headful under Xvfb, DevTools on 127.0.0.1:$CDP_INTERNAL_PORT)"
exec dbus-launch --exit-with-session google-chrome-stable \
    --user-data-dir="$PROFILE" \
    --remote-debugging-port="$CDP_INTERNAL_PORT" \
    --remote-allow-origins=* \
    --password-store=basic \
    --window-position=0,0 \
    --window-size="$WINDOW_SIZE" \
    --disable-gpu \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    "$@"
