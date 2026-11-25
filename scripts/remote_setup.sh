#!/usr/bin/env bash
# Provision a kube-labs machine (or any Ubuntu 24.04 host) to run
# Bonsai Image 4B (FLUX.2-klein-4B). Idempotent: safe to re-run.
#
# The 8-unit kube-labs machine has 16 GB of disk. The 2-bit Q2_K profile needs
# only ~5.8 GB of weights, but the fp16 fallback would push it to ~13 GB;
# either way every hundred megabytes matters, so this script reclaims what it
# can first.
set -euo pipefail

ROOT="${ROOT:-/srv}"
COMFY_DIR="$ROOT/ComfyUI"
VENV="$COMFY_DIR/.venv"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

export DEBIAN_FRONTEND=noninteractive

log "Disk before"
df -h / | tail -1

log "Reclaiming disk (docker/containerd/snap are not needed on this box)"
apt-get remove -y --purge \
  docker.io docker-ce docker-ce-cli docker-ce-rootless-extras \
  containerd containerd.io runc docker-buildx-plugin docker-compose-plugin \
  docker-buildx docker-compose-v2 snapd >/dev/null 2>&1 || true
rm -rf /var/lib/docker /var/lib/containerd /var/snap/docker /var/snapd \
  /var/lib/apt/lists/* /var/cache/apt/archives/* \
  /usr/share/doc/* /usr/share/man/* /usr/share/locale/* \
  /var/log/*.log /var/log/journal/* 2>/dev/null || true
apt-get clean

log "Installing base packages"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  python3-venv python3-pip python3-dev build-essential git curl wget ca-certificates \
  aria2 jq libgl1 libglib2.0-0 libgomp1 >/dev/null

log "Fetching ComfyUI"
mkdir -p "$ROOT"
if [ ! -d "$COMFY_DIR/comfy" ]; then
  rm -rf "$COMFY_DIR"
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR"
fi
# The git history is ~100 MB and cannot help us run the model.
rm -rf "$COMFY_DIR/.git"
cd "$COMFY_DIR"

log "Creating virtualenv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
pip install --no-cache-dir --quiet --upgrade pip wheel setuptools

log "Installing CPU PyTorch"
pip install --no-cache-dir --quiet \
  torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# ComfyUI depends on comfy-kitchen. The default wheel carries ~200 MB of CUDA
# kernels we cannot use on this box; the pure-Python wheel has the eager
# backend (which is what runs on CPU) and is a few hundred kilobytes.
log "Installing pure-Python comfy-kitchen"
CK_URL=$(curl -fsSL https://pypi.org/pypi/comfy-kitchen/0.2.42/json \
  | jq -r '.urls[] | select(.filename | endswith("py3-none-any.whl")) | .url' \
  | head -n 1)
if [ -n "$CK_URL" ]; then
  pip install --no-cache-dir --quiet --no-deps "$CK_URL"
  pip show comfy-kitchen | sed -n '1,3p'
else
  echo "  could not resolve the pure-Python wheel; falling back to the default build"
fi

log "Installing ComfyUI requirements"
pip install --no-cache-dir --quiet -r requirements.txt

# The stock city96 loader cannot be trusted with a GGUF whose architecture has
# to be inferred from tensor names. molbal's fork ("comfyui-gguf-reboot") is the
# community loader for GGUF diffusion models; it keeps the `UnetLoaderGGUF`
# node name, so the gateway graph works as-is. The 2-bit Q2_K flux.2-klein
# file uses only molbal's native quants (Q2_K/Q4_K/BF16/F32), so it loads
# through the fast path.
log "Installing GGUF loader (molbal/ComfyUI-GGUF)"
GGUF_DIR=custom_nodes/ComfyUI-GGUF
if ! grep -q "UnetLoaderGGUFDynamicVRAM" "$GGUF_DIR/nodes.py" 2>/dev/null; then
  rm -rf "$GGUF_DIR"
  git clone --depth 1 https://github.com/molbal/ComfyUI-GGUF.git "$GGUF_DIR"
fi
rm -rf "$GGUF_DIR/.git"
pip install --no-cache-dir --quiet -r "$GGUF_DIR/requirements.txt"

log "Preparing model folders"
mkdir -p models/diffusion_models models/text_encoders models/vae models/loras output
mkdir -p "$ROOT/models" "$ROOT/data"

log "Trimming caches"
rm -rf "$HOME/.cache/pip" 2>/dev/null || true
find "$COMFY_DIR" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

log "Disk after install"
df -h / | tail -1
du -sh "$VENV" "$COMFY_DIR" 2>/dev/null || true

log "Done. Run scripts/remote_models.sh to fetch weights."
