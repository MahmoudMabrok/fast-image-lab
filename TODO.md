# Fast Image Lab — Bonsai Image 4B (distilled FLUX.2-Klein-4B) on kube-labs

Self-hosted text-to-image studio: FastAPI HTTP wrapper + ComfyUI (CPU) engine
running the distilled FLUX.2-Klein-4B build that Bonsai Image 4B is quantized
from (Bonsai's 1-bit Q1_0 file is unloadable — no Q1_0 dequantizer exists in
any current toolchain — so the CPU build uses its 2-bit Q2_K GGUF), exposed
via a cloudflared quick tunnel. Images are ephemeral (five hours).

## Build

### 2026-11-25 — rename to fast-image-lab + backport ImageLab features

Rename scope: branding + MCP identity only. Internal identifiers stay:
`ANOTHERLAB_*` env vars, `anotherlab.service` / `anotherlab-tunnel.service`,
`/srv/anotherlab`, `/etc/anotherlab.env`, browser localStorage keys.

- [ ] Repo directory renamed to `/Users/appleworld/Documents/fast-image-lab`
- [ ] User-visible "Fast Image Lab" strings → "Fast Image Lab" (web, README, AGENTS, docs, Makefile, scripts, systemd `Description=`)
- [ ] MCP identity renamed: serverInfo `fast-image-lab`, resource `fast-image-lab://engine`, `/api/config` `mcp.name`
- [ ] TTL default `18000` s (5 hours) in `config.py` + `.env.example` + docs copy
- [ ] Store: `styles` / `quality` columns and idempotent migration for live DBs
- [ ] Workflow: `STYLE_SUFFIXES` + `render_prompt()`; engine folds styles into the text prompt
- [ ] API: `/api/generate` accepts `styles` / `quality`; every job response carries them
- [ ] Engine: lost-prompt fail-fast (`prompt_vanish_seconds=60`) frees the worker
- [ ] UI: `h:mm:ss` TTL clock, 5-hour copy, warn/crit at 30 / 10 minutes
- [ ] UI: per-card recipe details (frame · steps · cfg · seed · quality · looks) + re-run restores the recipe
- [ ] UI: live progress without refresh (SSE-starvation detection + 1 s poll fallback, wake handlers)
- [ ] UI: generate sends structured `styles` / `quality` instead of a suffix-baked prompt
- [ ] `make test` green with new coverage (store, workflow, engine, API, MCP)
- [ ] Deploy to `bonsai`; `comfyui` + `anotherlab` services `active (running)`
- [ ] Quick-tunnel URL re-verified publicly (health + UI + MCP)
- [ ] One public generation through the new UI path
- [ ] README public URL + verification date updated

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
