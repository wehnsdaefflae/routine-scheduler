#!/usr/bin/env bash
# Bring up the agent-desktop host: the guests' network slots, the doors in front of them, and
# the broker that boots one VM per routine on demand. See docs/desktop-sessions.md.
#
# Runs as root only for what needs root inside this container's own network namespace — one tap
# per slot, NAT out, the firewall that keeps every guest off private networks (and off each
# other), the DNS forwarder — and to hand the state dir and /dev/kvm to `mark`. Then drops to
# `mark`, which runs the token proxy, noVNC and the broker (the VMMs are the broker's children):
# the same dance the engine and chrome entrypoints do.
set -euo pipefail

log() { printf '[desktop] %s\n' "$*" >&2; }

STATE="${DESKTOP_STATE:-/home/mark/desktop-vm}"
SLOTS="${DESKTOP_MAX_VMS:-2}"
AGENT_PORT=8790
BROKER_PORT=8789
VNC_PORT=6080
VNC_INTERNAL_PORT=6081

# The agent port moves the mouse and types on a whole computer, and noVNC is its screen; neither
# authenticates anything of its own. Refuse to start without the token rather than open an
# unauthenticated door that looks guarded — the same rule the chrome sidecar keeps.
if [ -z "${DESKTOP_VM_TOKEN:-}" ]; then
    log "FATAL: DESKTOP_VM_TOKEN is not set. The agent and noVNC ports have no authentication"
    log "       of their own, so this container will not start an unauthenticated desktop."
    log "       Set it in .env (see deploy/DOCKER.md, 'The agent desktop')."
    exit 78    # EX_CONFIG
fi
# The OPERATOR's token is a second secret: it opens every screen (noVNC) and the fleet listing,
# so it is never the routines' DESKTOP_VM_TOKEN — with that one a routine could open another
# routine's desktop through its screen and bypass the broker's per-routine identity.
if [ -z "${DESKTOP_OPERATOR_TOKEN:-}" ] || [ "$DESKTOP_OPERATOR_TOKEN" = "$DESKTOP_VM_TOKEN" ]; then
    log "FATAL: DESKTOP_OPERATOR_TOKEN must be set, and differ from DESKTOP_VM_TOKEN. It guards"
    log "       every desktop's screen; the routines' token must not open one."
    exit 78
fi
if [ ! -c /dev/kvm ]; then
    log "FATAL: /dev/kvm is not available in this container. The desktops are KVM guests; the"
    log "       host needs hardware virtualisation and compose must pass the device through."
    exit 78
fi

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$STATE"
    chown mark:mark "$STATE"

    # /dev/kvm keeps the HOST's group id, which names nothing in this image.
    kvm_gid="$(stat -c %g /dev/kvm)"
    kvm_group="$(getent group "$kvm_gid" | cut -d: -f1 || true)"
    if [ -z "$kvm_group" ]; then
        kvm_group=kvmhost
        groupadd -g "$kvm_gid" "$kvm_group"
    fi
    usermod -aG "$kvm_group" mark

    # One /30 per slot: slot i's guest is 192.168.249.(4i+2) behind gateway .(4i+1), on tap
    # vmtap<i> owned by `mark`, so the broker's VMMs open it without privileges. vms.slot_net
    # computes the same addresses.
    listen=()
    for ((i = 0; i < SLOTS; i++)); do
        tap="vmtap$i"
        gw="192.168.249.$((4 * i + 1))"
        ip link show "$tap" >/dev/null 2>&1 || ip tuntap add dev "$tap" mode tap user mark
        ip addr replace "$gw/30" dev "$tap"
        ip link set "$tap" up
        listen+=("--listen-address=$gw")
    done

    # Out to the internet: yes. Into any private range — the LAN, this host's other containers,
    # the engine's console, the signed-in browser, ANOTHER ROUTINE'S DESKTOP — no: a guest
    # renders whatever page an agent opens, and nothing it runs should be one hop from this
    # instance's own services or another routine's screen. Into THIS container: DNS only. Rules
    # are APPENDED, never flushed: Docker's embedded DNS lives in this namespace's nat table.
    iptables -P FORWARD DROP
    iptables -A FORWARD -o vmtap+ -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    for private in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
        iptables -A FORWARD -i vmtap+ -d "$private" -j DROP
    done
    iptables -A FORWARD -i vmtap+ -j ACCEPT
    iptables -t nat -A POSTROUTING -s 192.168.249.0/24 ! -o vmtap+ -j MASQUERADE
    iptables -A INPUT -i vmtap+ -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A INPUT -i vmtap+ -p udp --dport 53 -j ACCEPT
    iptables -A INPUT -i vmtap+ -p tcp --dport 53 -j ACCEPT
    iptables -A INPUT -i vmtap+ -j DROP

    # The guests cannot reach Docker's resolver (127.0.0.11 is this namespace's loopback), so a
    # forwarder on each slot's gateway relays to whatever this container resolves through — a
    # guest resolves exactly what the container does, NAT64 and all.
    dnsmasq --resolv-file=/etc/resolv.conf --no-hosts --bind-interfaces "${listen[@]}" \
        --user=nobody

    log "$SLOTS slot(s) ready; dropping to mark"
    exec gosu mark "$0" "$@"
fi

# ---- as mark --------------------------------------------------------------------------------

mkdir -p "$STATE/run"
: > "$STATE/run/vnc-tokens"

# One noVNC for every desktop: ?path=websockify%3Ftoken%3D<desktop name> picks the screen, from
# the token file the broker rewrites whenever a desktop starts or stops.
log "starting noVNC on 127.0.0.1:$VNC_INTERNAL_PORT (fronted by the auth proxy)"
websockify --web=/usr/share/novnc --token-plugin TokenFile \
    --token-source "$STATE/run/vnc-tokens" "127.0.0.1:$VNC_INTERNAL_PORT" \
    >/dev/null 2>&1 &

# Two doors, two tokens: the broker answers every routine (and proves which one is calling);
# the screens answer only the operator — the console's relay.
log "fronting the broker :$AGENT_PORT (routines' token) and noVNC :$VNC_PORT (operator's token)"
python3 /usr/local/bin/browser-auth-proxy.py --token-env DESKTOP_VM_TOKEN \
    --map "0.0.0.0:${AGENT_PORT}=127.0.0.1:${BROKER_PORT}:broker" &
python3 /usr/local/bin/browser-auth-proxy.py --token-env DESKTOP_OPERATOR_TOKEN \
    --map "0.0.0.0:${VNC_PORT}=127.0.0.1:${VNC_INTERNAL_PORT}:novnc" &

# The broker is the foreground process: tini hands it the container's SIGTERM, and it powers
# every desktop off (the guest's ACPI button, a kill only after the grace) before it exits.
cd /usr/local/lib/desktop-broker
exec python3 broker.py
