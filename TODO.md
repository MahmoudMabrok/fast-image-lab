# AnotherLab — Bonsai Image 4B (distilled FLUX.2-Klein-4B) on kube-labs

Self-hosted text-to-image studio: FastAPI HTTP wrapper + ComfyUI (CPU) engine
running the distilled FLUX.2-Klein-4B build that Bonsai Image 4B is quantized
from (Bonsai's 1-bit Q1_0 file is unloadable — no Q1_0 dequantizer exists in
any current toolchain — so the CPU build uses its 2-bit Q2_K GGUF), exposed
via a cloudflared quick tunnel. Images are ephemeral.

## Build

(in progress — add new tasks here)

## Completed

### 2026-11-25 — repo, VM, tunnel, and first generations
- [x] Research model + CPU paths (1-bit GGUF exists; gemlite is CUDA-only)
- [x] Run mode decided: ComfyUI in host venv + systemd (proven ImageLab path); docker-linux is the VM base image
- [x] Scaffold repo (gateway, web, scripts, systemd, tests) — local, verified
- [x] `make test` green locally — 30 tests pass
- [x] Provision docker-linux 8u VM (`bonsai`) — 172.16.0.15, vm_d3ab54044066, running
- [x] Provision ComfyUI: `make setup MACHINE=bonsai` (CPU torch, ComfyUI 0.46.0, molbal GGUF node)
- [x] **Risk test:** `detect_arch` on the 149 tensor names → `flux`; graph rebuilt to the official FLUX.2 Distilled custom-sampler pipeline (`Flux2Scheduler` + `SamplerCustomAdvanced`), 30/30 tests green
- [x] Download weights — transformer, Qwen3-4B fp4 TE, FLUX.2 VAE — `make models`
- [x] Deploy gateway + systemd services — `make deploy` (comfyui + anotherlab both `active (running)`, `/api/health` → `ready: true`)
- [x] **1-bit verdict:** live generation failed with `NotImplementedError: Dequantization for Q1_0 is not yet implemented` → molbal's table (Q2_K–Q8_0, IQ2–IQ4) + the `gguf` package both lack a 1-bit decoder; the Green-Sky "ternary" files use GGML type 42 (unrecognized even by `gguf` 0.19.0). 1-bit is unloadable on CPU
- [x] **Transformer picked:** `flux-2-klein-4b-Q2_K.gguf` (unsloth's 2-bit Q2_K of the distilled FLUX.2-Klein-4B; 149 tensors, identical layout; `gguf_sd_loader` → arch `flux`, `LOAD OK`) downloaded to the VM
- [x] Re-point gateway env at the Q2_K file — `/etc/anotherlab.env` → `flux-2-klein-4b-Q2_K.gguf`, gateway restarted, `/api/health` shows the new UNET
- [x] One real 1024²/4-step generation succeeds via local HTTP — `img_6sGQsQPjz_qZ`, cold process, 414 s wall (incl. one-time weight load), 1.45 MB PNG
- [x] Deploy cloudflared tunnel — `make tunnel`; URL `https://views-finder-red-cities.trycloudflare.com` (health + UI + MCP verified public)
- [x] One public generation verified over the tunnel — `img_NFONEGWGskdx` (512²/4 steps), submit→poll→image all via `https://views-finder-red-cities.trycloudflare.com`, 147 s warm-process round trip, image fetched from the public URL
- [x] URL + model change written into docs; committed — first commit `69c5e4c` (44 files), URL in README with 2026-11-25 verification date
