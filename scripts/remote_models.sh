#!/usr/bin/env bash
# Download the primary CPU profile weights for Bonsai Image 4B (~5.8 GiB):
#   transformer : flux-2-klein-4b-Q2_K.gguf          ~1.7 GiB (2-bit GGUF,
#                 the distilled FLUX.2-Klein-4B that Bonsai is quantized from;
#                 Bonsai's own 1-bit Q1_0 file cannot be dequantized by any
#                 current toolchain)
#   text encoder: qwen_3_4b_fp4_flux2.safetensors    ~3.9 GiB
#   vae         : flux2-vae.safetensors               ~0.3 GiB
#
# If the Q2_K GGUF fails the arch test on this host, run
# remote_models_fallback.sh to fetch the 7.75 GiB fp16 transformer and flip
# ANOTHERLAB_UNET_LOADER / ANOTHERLAB_UNET in /etc/anotherlab.env.
set -euo pipefail

ROOT="${ROOT:-/srv}"
COMFY_DIR="$ROOT/ComfyUI"
HF="${HF_ENDPOINT:-https://huggingface.co}"
DM="$COMFY_DIR/models/diffusion_models"
TE="$COMFY_DIR/models/text_encoders"
VAE="$COMFY_DIR/models/vae"
mkdir -p "$DM" "$TE" "$VAE"

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

echo "==> Diffusion model (2-bit Q2_K GGUF of the distilled 4B build)"
dl "$HF/unsloth/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q2_K.gguf" \
   "$DM/flux-2-klein-4b-Q2_K.gguf"

echo "==> Text encoder (Qwen3 4B fp4, FLUX.2 build)"
dl "$HF/Comfy-Org/FLux-2-Klein-4B/resolve/main/split_files/text_encoders/qwen_3_4b_fp4_flux2.safetensors" \
   "$TE/qwen_3_4b_fp4_flux2.safetensors"

echo "==> VAE"
dl "$HF/Comfy-Org/FLux-2-Klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors" \
   "$VAE/flux2-vae.safetensors"

echo "==> Sizes"
du -sh "$DM"/* "$TE"/* "$VAE"/* 2>/dev/null | sort -h
df -h / | tail -1
