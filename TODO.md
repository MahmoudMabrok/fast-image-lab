# Fast Image Lab — Bonsai Image 4B (distilled FLUX.2-Klein-4B) on kube-labs

Self-hosted text-to-image studio: FastAPI HTTP wrapper + ComfyUI (CPU) engine
running the distilled FLUX.2-Klein-4B build that Bonsai Image 4B is quantized
from (Bonsai's 1-bit Q1_0 file is unloadable — no Q1_0 dequantizer exists in
any current toolchain — so the CPU build uses its 2-bit Q2_K GGUF), exposed
via a cloudflared quick tunnel. Images are ephemeral (five hours).

## Build

### 2026-11-25 — named tunnel config audit on `bonsai` (read-only)

- [x] Inspected `/etc/cloudflared/config.yml` — tunnel `ab3ea48d-…` (`fast-image-lab`), ingress `fast-image-lab.mo3ta.top` → `http://127.0.0.1:3000`
- [x] Inspected `/root/.cloudflared/` — `cert.pem` + tunnel credentials; `cloudflared tunnel list` shows `fast-image-lab`, `share`, `voice`
- [x] Confirmed `cloudflared.service` (enabled, active) runs the named tunnel from `/etc/cloudflared/config.yml`
- [x] Confirmed port mismatch: nothing listens on `3000`; gateway `uvicorn` listens on `8080` → public host returns 502
- [x] Confirmed duplicate tunnel: `anotherlab-tunnel.service` still runs a quick tunnel to `8080` (`views-finder-red-cities.trycloudflare.com`, 200)
- [x] Confirmed no Access policy / `ANOTHERLAB_API_KEY=` empty → stable hostname would be unauthenticated
- [ ] Point ingress at `http://127.0.0.1:8080` (or repoint the gateway) and verify `fast-image-lab.mo3ta.top/api/health` → 200
- [ ] Retire `anotherlab-tunnel.service` + update `ANOTHERLAB_PUBLIC_URL` to the named host (decision needed)
- [ ] Set `ANOTHERLAB_API_KEY` before the named host is publicly usable

## Completed

### Done 2026-11-26: rename to fast-image-lab + backport ImageLab features

Rename scope: branding + MCP identity only. Internal identifiers stay:
`ANOTHERLAB_*` env vars, `anotherlab.service` / `anotherlab-tunnel.service`,
`/srv/anotherlab`, `/etc/anotherlab.env`, browser localStorage keys.

- [x] Repo directory renamed to `/Users/appleworld/Documents/fast-image-lab`
- [x] User-visible "Fast Image Lab" strings → "Fast Image Lab" (web, README, AGENTS, docs, Makefile, scripts, systemd `Description=`)
- [x] MCP identity renamed: serverInfo `fast-image-lab`, resource `fast-image-lab://engine`, `/api/config` `mcp.name`
- [x] TTL default `18000` s (5 hours) in `config.py` + `.env.example` + docs copy (README env table, ARCHITECTURE, MCP, AGENTS all 5-hour)
- [x] Store: `styles` / `quality` columns and idempotent migration for live DBs
- [x] Workflow: `STYLE_SUFFIXES` + `render_prompt()`; engine folds styles into the text prompt
- [x] API: `/api/generate` accepts `styles` / `quality`; every job response carries them
- [x] Engine: lost-prompt fail-fast (`prompt_vanish_seconds=60`) frees the worker
- [x] UI: `h:mm:ss` TTL clock, 5-hour copy, warn/crit at 30 / 10 minutes
- [x] UI: per-card recipe details (frame · steps · cfg · seed · quality · looks) + re-run restores the recipe
- [x] UI: live progress without refresh (SSE-starvation detection + 1 s poll fallback, wake handlers)
- [x] UI: generate sends structured `styles` / `quality` instead of a suffix-baked prompt
- [x] `make test` green with new coverage (store, workflow, engine, API, MCP) — 34/34
- [x] Deploy to `bonsai` (`make deploy`); `comfyui` + `anotherlab` `active (running)`; `/api/health` `ready: true`
- [x] `bonsai` `/etc/anotherlab.env`: `ANOTHERLAB_TTL_SECONDS` 300 → 18000, restart, `/api/health` reports `ttl_seconds: 18000`
- [x] Quick-tunnel URL re-verified publicly: `https://views-finder-red-cities.trycloudflare.com` — `/api/health` ready, UI page (Fast Image Lab, 5-hour copy), served `app.js` carries live-progress/recipe code (grep 11), MCP `initialize` → `serverInfo.fast-image-lab`
- [x] One public generation (256×256) through the new UI path — `img_rrm36DKTHujS`: `styles:"cinematic"`, `quality:"draft"`, clean stored prompt `a lighthouse in a storm`, progress 0.05→1.0 over the poll path, 62 s; text encoder received `… cinematic lighting, anamorphic lens, film still, dramatic contrast` (render_prompt live); valid PNG served from the public URL
- [x] README public URL + verification date updated (2026-11-26)
- [x] Everything pushed to `github.com/MahmoudMabrok/fast-image-lab` — `main` (clean baseline) + `wip/imagelab-backport` (rename + backport + tests, 2 commits `142c62c`, `1d43fd0`)

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
