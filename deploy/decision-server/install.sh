#!/usr/bin/env bash
# Install (or refresh) the decision server on a GPU box, as the account that will run it.
#
#   scp -r deploy/decision-server <user>@<box>: && ssh <user>@<box> 'bash decision-server/install.sh'
#
# Idempotent: the venv, the weights and the token are kept when already right; the two source
# files and the systemd USER unit are rewritten every time. Everything lands under
# $DECISION_SERVER_HOME (default ~/decision-server) and needs no root — except that a user
# service only outlives the login with LINGER on, which this script checks and names.
#
# What it pins, and why each pin is there (docs/decision-models.md):
#   llama-cpp-python 0.3.36 from the cu124 index — 0.3.35's cu124 wheel SIGILLs on this card,
#     cu126 does not exist, and --index-url (not --extra-) is what stops PyPI's CPU wheel winning
#   Qwen3-VL-8B-Instruct Q4_K_M + mmproj Q8_0 at one repo revision, sha256-verified — the
#     exact weights benchmarked on predator (R2254/R2258)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${DECISION_SERVER_HOME:-$HOME/decision-server}
PORT=${DECISION_SERVER_PORT:-8790}
CTX=${DECISION_SERVER_CTX:-4096}
UV=${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}
REPO=Qwen/Qwen3-VL-8B-Instruct-GGUF
REV=f982a07559d4a2f6c8744d840bf6fccab30eea96
WEIGHTS=Qwen3VL-8B-Instruct-Q4_K_M.gguf
WEIGHTS_SHA=67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2
MMPROJ=mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf
MMPROJ_SHA=c6ba85508d82f42590e6eb77d5340369ab6fecf107a7561d809523d8aa5f3bfd
TOKEN_FILE=$HOME/.config/decision-server/token

[ -x "$UV" ] || { echo "uv not found (set UV=/path/to/uv)" >&2; exit 1; }
mkdir -p "$ROOT/models"
install -m 0644 "$HERE/decision_protocol.py" "$ROOT/decision_protocol.py"
install -m 0755 "$HERE/decision_server.py" "$ROOT/decision_server.py"

PY=$ROOT/.venv/bin/python
[ -x "$PY" ] || "$UV" venv -q --python 3.12 "$ROOT/.venv"
"$UV" pip install -q --python "$PY" typing-extensions 'numpy<3' diskcache jinja2 pillow \
    nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
if [ "$("$PY" -c 'import llama_cpp; print(llama_cpp.__version__)' 2>/dev/null)" != 0.3.36 ]; then
    "$UV" pip install -q --python "$PY" --no-deps --force-reinstall \
        --index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 llama-cpp-python==0.3.36
fi

fetch() {  # file sha256 — download at the pinned revision unless already verified
    local f=$1 want=$2 dest=$ROOT/models/$1
    if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ]; then return; fi
    curl -fSL --retry 5 -C - -o "$dest" "https://huggingface.co/$REPO/resolve/$REV/$f"
    [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ] || { echo "sha256 mismatch: $f" >&2; exit 1; }
}
fetch "$WEIGHTS" "$WEIGHTS_SHA"
fetch "$MMPROJ" "$MMPROJ_SHA"

if [ ! -s "$TOKEN_FILE" ]; then
    mkdir -p "$(dirname "$TOKEN_FILE")" && chmod 700 "$(dirname "$TOKEN_FILE")"
    (umask 077 && "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))' > "$TOKEN_FILE")
    echo "generated a bearer token in $TOKEN_FILE — the scheduler's decision endpoint needs it"
fi

SP=$ROOT/.venv/lib/python3.12/site-packages
UNIT=$HOME/.config/systemd/user/decision-server.service
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=Decision endpoint (OpenAI Decisions protocol) over Qwen3-VL-8B
After=network-online.target

[Service]
Environment=LD_LIBRARY_PATH=$SP/nvidia/cuda_runtime/lib:$SP/nvidia/cublas/lib:$SP/nvidia/cuda_nvrtc/lib
ExecStart=$PY $ROOT/decision_server.py --gguf $ROOT/models/$WEIGHTS --mmproj $ROOT/models/$MMPROJ --token-file $TOKEN_FILE --port $PORT --ctx $CTX
Restart=on-failure
RestartSec=10
Nice=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable -q decision-server.service
systemctl --user restart decision-server.service
sleep 2
curl -fsS "http://127.0.0.1:$PORT/health" && echo
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
    echo "LINGER IS OFF: the service stops at your last logout and does not start at boot." >&2
    echo "Turn it on once with:  sudo loginctl enable-linger $USER" >&2
fi
