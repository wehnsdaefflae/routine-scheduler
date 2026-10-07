#!/usr/bin/env bash
# One image, many VMs: everything that differs between them arrives on the kernel command line
# (the broker writes it per VM) and is applied here, before the network comes up.
#   rsched.ip=192.168.249.6/30  rsched.gw=192.168.249.5  rsched.tz=Europe/Berlin
#   rsched.name=routines--foo
set -euo pipefail

arg() { tr ' ' '\n' < /proc/cmdline | sed -n "s/^rsched\.$1=//p" | tail -1; }

ip="$(arg ip)"; gw="$(arg gw)"; tz="$(arg tz)"; name="$(arg name)"
if [ -n "$ip" ] && [ -n "$gw" ]; then
    mkdir -p /run/systemd/network
    printf '[Match]\nName=eth0\n\n[Network]\nAddress=%s\nGateway=%s\nDNS=%s\n' \
        "$ip" "$gw" "$gw" > /run/systemd/network/10-eth0.network
    printf 'nameserver %s\n' "$gw" > /etc/resolv.conf
fi
if [ -n "$tz" ] && [ -e "/usr/share/zoneinfo/$tz" ]; then
    ln -sf "/usr/share/zoneinfo/$tz" /etc/localtime
    printf '%s\n' "$tz" > /etc/timezone
fi
if [ -n "$name" ]; then
    printf '%s\n' "$name" > /etc/desktop-name
fi
