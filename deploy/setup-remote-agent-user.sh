#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup-remote-agent-user.sh — create a restricted local user for the
# routine-scheduler `remote` util to SSH into, instead of logging in as an
# admin account. Run this ON THE REMOTE (GPU/build) HOST. See
# docs/remote-machines.md § "Creating the restricted remote user".
#
# WHAT IT CREATES (idempotent — safe to re-run):
#   * user $AGENT_USER: no sudo, locked password, key-only login, /bin/bash
#   * a standalone home $AGENT_HOME (its OWN dir — never nested in an admin's
#     home), owner $AGENT_USER:$AGENT_USER, mode 2770 (setgid)
#   * optional co-owner $AGENT_COOWNER (an admin) added to the agent's group,
#     so that admin keeps full read/write access to everything the agent makes
#   * SSH pubkey stored ROOT-OWNED at /etc/ssh/authorized_keys/$AGENT_USER and
#     scoped by a `Match User $AGENT_USER` sshd block — StrictModes-safe for a
#     group-writable home, and the agent cannot rewrite its own trust anchor
#   * membership in video/render (GPU robustness; NVIDIA device nodes are
#     usually 0666 already, so CUDA/PyTorch/TF need no extra grant)
#
# It does NOT touch any other user's home, does NOT grant sudo, and validates
# sshd with `sshd -t` before reloading (restoring the backup on failure).
#
# USAGE (run as root):
#   AGENT_PUBKEY='ssh-ed25519 AAAA...' AGENT_COOWNER=mark \
#     sudo -E bash setup-remote-agent-user.sh
# or drop the public key in a file `agent.pub` beside this script and just:
#   AGENT_COOWNER=mark sudo -E bash setup-remote-agent-user.sh
# Optional end-to-end SSH loopback test (pass the matching PRIVATE key):
#   ... bash setup-remote-agent-user.sh /path/to/agent_ed25519
#
# TUNABLES (env):  AGENT_USER=agent  AGENT_HOME=/home/$AGENT_USER  AGENT_COOWNER=
# ---------------------------------------------------------------------------
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="${AGENT_HOME:-/home/$AGENT_USER}"
AGENT_COOWNER="${AGENT_COOWNER:-}"                       # admin to co-own (optional)
SSH_AK_DIR="/etc/ssh/authorized_keys"
SSHD_CONF="/etc/ssh/sshd_config"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="rsched remote-agent ($AGENT_USER)"

# public key: env var wins, else a file `agent.pub` beside the script
AGENT_PUBKEY="${AGENT_PUBKEY:-$(cat "$SCRIPT_DIR/agent.pub" 2>/dev/null || true)}"

if [[ $EUID -ne 0 ]]; then echo "ERROR: run me as root (sudo)." >&2; exit 1; fi
if [[ -z "${AGENT_PUBKEY// }" ]]; then
  echo "ERROR: no public key. Set AGENT_PUBKEY=... or put it in $SCRIPT_DIR/agent.pub" >&2
  exit 1
fi

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# 1) user + its own primary group ------------------------------------------
say "user: $AGENT_USER (no sudo, locked password, key-only)"
if ! getent passwd "$AGENT_USER" >/dev/null; then
  useradd --create-home --home-dir "$AGENT_HOME" \
          --shell /bin/bash --user-group "$AGENT_USER"
fi
usermod --home "$AGENT_HOME" "$AGENT_USER"
usermod -aG video,render "$AGENT_USER"
passwd -l "$AGENT_USER" >/dev/null 2>&1 || true

# 2) the agent's own home (shared with the co-owner, closed to the world) ---
say "home: $AGENT_HOME (owner $AGENT_USER:$AGENT_USER, mode 2770)"
mkdir -p "$AGENT_HOME"
chown "$AGENT_USER":"$AGENT_USER" "$AGENT_HOME"
chmod 2770 "$AGENT_HOME"                                 # setgid: new files stay group $AGENT_USER
if [[ ! -e "$AGENT_HOME/.bashrc" ]]; then
  printf 'umask 002\n[ -f /etc/bashrc ] && . /etc/bashrc\n' > "$AGENT_HOME/.bashrc"
  chown "$AGENT_USER":"$AGENT_USER" "$AGENT_HOME/.bashrc"; chmod 664 "$AGENT_HOME/.bashrc"
fi

# 3) optional admin co-owner (full access via the agent's group) -----------
if [[ -n "$AGENT_COOWNER" ]]; then
  if getent passwd "$AGENT_COOWNER" >/dev/null; then
    say "co-owner: adding $AGENT_COOWNER to group $AGENT_USER (full access to $AGENT_HOME)"
    usermod -aG "$AGENT_USER" "$AGENT_COOWNER"
    echo "  note: $AGENT_COOWNER must re-login for the new group to take effect."
  else
    echo "WARN: co-owner '$AGENT_COOWNER' does not exist — skipping."
  fi
fi

# 4) root-owned authorized_keys (StrictModes-immune for a group-writable home)
say "ssh key: $SSH_AK_DIR/$AGENT_USER (root-owned; agent can't edit it)"
install -d -o root -g root -m 755 "$SSH_AK_DIR"
printf '%s\n' "$AGENT_PUBKEY" > "$SSH_AK_DIR/$AGENT_USER"
chown root:root "$SSH_AK_DIR/$AGENT_USER"; chmod 644 "$SSH_AK_DIR/$AGENT_USER"

# 5) sshd: scope the key file + light hardening to this user ----------------
say "sshd: Match User $AGENT_USER block"
if ! grep -qF "$MARKER" "$SSHD_CONF"; then
  cp -a "$SSHD_CONF" "$SSHD_CONF.bak-rsched"             # first-run backup only
  cat >> "$SSHD_CONF" <<EOF

# >>> $MARKER >>>
Match User $AGENT_USER
    AuthorizedKeysFile $SSH_AK_DIR/%u
    PasswordAuthentication no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY yes
Match all
# <<< $MARKER <<<
EOF
fi
if sshd -t; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload
  echo "sshd config valid; reloaded."
else
  echo "ERROR: sshd -t failed — restoring $SSHD_CONF.bak-rsched and aborting." >&2
  [[ -f "$SSHD_CONF.bak-rsched" ]] && cp -a "$SSHD_CONF.bak-rsched" "$SSHD_CONF"
  exit 1
fi

# 6) self-checks ------------------------------------------------------------
say "self-checks"
echo "-- id:"; id "$AGENT_USER"
echo "-- home writable by agent:"
sudo -u "$AGENT_USER" -H bash -lc 'cd ~ && pwd && ( touch .setup_probe && rm -f .setup_probe && echo "  writable: OK" )'
echo "-- agent can see GPU device nodes:"
sudo -u "$AGENT_USER" -H bash -lc 'ok=1; for d in /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm; do [ -e "$d" ] || continue; { [ -r "$d" ] && [ -w "$d" ]; } && echo "  $d rw: OK" || { echo "  $d: NOT accessible"; ok=0; }; done; [ $ok = 1 ] || echo "  (if a node is missing, the NVIDIA driver may need a reload/reboot)"'
echo "-- nvidia-smi as agent (skip if the host has no NVIDIA GPU):"
sudo -u "$AGENT_USER" -H bash -lc 'command -v nvidia-smi >/dev/null && nvidia-smi -L 2>&1 | sed "s/^/  /" || echo "  (nvidia-smi not present)"'

# 7) OPTIONAL end-to-end SSH loopback test (pass a private key as $1) -------
if [[ "${1:-}" != "" && -f "${1}" ]]; then
  say "loopback ssh test with key: $1"
  ssh -i "$1" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
      "$AGENT_USER"@localhost 'echo "  ssh OK as $(whoami) in $(pwd)"' \
    || echo "  loopback ssh test FAILED — check 'journalctl -u ssh' for StrictModes errors"
fi

say "done. Restricted user '$AGENT_USER' is ready (home $AGENT_HOME)."
cat <<NEXT

NEXT (routine-scheduler side):
  1. Settings -> Secrets: store the matching PRIVATE key under a key_var name.
  2. Settings -> Machines: point the machine at user '$AGENT_USER', that key_var,
     and workdir '$AGENT_HOME'. Keep the pinned host key. Then click "test".

UNINSTALL (reverts this script):
  sudo userdel -r $AGENT_USER                             # -r also removes $AGENT_HOME
  ${AGENT_COOWNER:+sudo gpasswd -d $AGENT_COOWNER $AGENT_USER 2>/dev/null || true}
  sudo groupdel $AGENT_USER 2>/dev/null || true
  sudo rm -f $SSH_AK_DIR/$AGENT_USER
  sudo cp -a $SSHD_CONF.bak-rsched $SSHD_CONF && sudo systemctl reload ssh
NEXT
