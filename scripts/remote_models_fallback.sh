#!/usr/bin/env bash
# Download the fp16 fallback transformer for Bonsai Image 4B (~7.75 GiB):
#   transformer : flux-2-klein-4b.safetensors   ~7.75 GiB (fp16, UNETLoader)
#
# Bonsai is the *distilled* FLUX.2-klein-4B, so the fallback must be the
# distilled variant (flux-2-klein-4b), NOT the base variant
# (flux-2-klein-base-4b) — the base needs many more sampling steps.
# Use this only if the Q2_K GGUF fails ComfyUI's arch detection on this host.
# Then point /etc/anotherlab.env at:
#   ANOTHERLAB_UNET=flux-2-klein-4b.safetensors
#   ANOTHERLAB_UNET_LOADER=UNETLoader
# and restart the gateway + ComfyUI.
set -euo pipefail

ROOT="${ROOT:-/srv}"
COMFY_DIR="$ROOT/ComfyUI"
HF="${HF_ENDPOINT:-https://huggingface.co}"
DM="$COMFY_DIR/models/diffusion_models"
mkdir -p "$DM"

dl() { # dl <url> <dest>
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then
    echo "  have $(basename "$dest")"
    return
  fi
  echo "  fetching $(basename "$dest")"
  aria2c -q -x 8 -s 8 -k 4M --file-allocation=none --allow-overwrite=true \
    --dir "$(dirname "$dest")" --out "$(basename "$dest")" "$url"
}

echo "==> Diffusion model (fp16 distilled, UNETLoader)"
dl "$HF/Comfy-Org/FLux-2-Klein-4B/resolve/main/split_files/diffusion_models/flux-2-klein-4b.safetensors" \
   "$DM/flux-2-klein-4b.safetensors"

echo "==> Sizes"
du -sh "$DM"/* 2>/dev/null | sort -h
df -h / | tail -1
