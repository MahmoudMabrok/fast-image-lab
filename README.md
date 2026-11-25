# AnotherLab

A small, self-hosted image generation studio for **[Bonsai Image 4B](https://huggingface.co/Comfy-Org/FLux-2-Klein-4B)** — a quantized distillation of FLUX.2-Klein-4B (4B DiT + Qwen3-4B text encoder + FLUX.2 VAE) that runs on CPU. It gives you a real UI and exposes the same engine to AI agents over the Model Context Protocol.

Every generated PNG is deleted **five minutes after it is ready**. That is the point: this is a sketchpad, not a gallery.

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Browser (vanilla JS UI)     │        │  Agent (Claude Code, Codex,  │
│  REST + SSE                  │        │  Cursor…) over MCP           │
└──────────────┬───────────────┘        └──────────────┬───────────────┘
               │                                       │
               ▼                                       ▼
      ┌────────────────────────────────────────────────────────┐
      │  FastAPI gateway :8080                                 │
      │  queue · SQLite · SSE · 5-minute reaper · MCP at /mcp  │
      └───────────────────────────┬────────────────────────────┘
                                  │ HTTP + WebSocket
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │  ComfyUI :8188 (headless, CPU)                         │
      │  Bonsai 4B · Q2_K GGUF · Qwen3-4B fp4 · FLUX.2 VAE     │
      │  4 steps · euler · FlowMatch · CFG 1 · no LoRA         │
      └────────────────────────────────────────────────────────┘
```

## What you get

- **A proper studio UI** — prompt composer, style presets, aspect ratios, quality tiers, live progress over SSE, download (or opt-in auto-download, remembered per browser), cancel, delete-now, and a recent strip with countdowns.
- **MCP for agents** — `generate_image`, `get_generation`, `cancel_generation`, `engine_info`. Images come back inline as base64 and are also served over HTTP. Agents only see the jobs they start (no listing endpoint).
- **Self-destructing images** — the reaper runs every two seconds and removes files whose TTL expired. Metadata is kept for a day so the UI can show what happened.
- **A CPU-native quantized model** — the transformer is a 2-bit Q2_K GGUF (~1.7 GB) of the distilled FLUX.2-Klein-4B build (the same weights Bonsai Image 4B quantizes to 1 bit), the whole reason this build fits on a 16 GB kube-labs disk. No LoRA: the step/guidance distillation is baked into the weights.
- **No front-end build step** — the UI is three static files served by the gateway.

## How it works

Bonsai Image 4B is distillation of FLUX.2-Klein-4B. The distillation makes three things different from a normal flux pipeline:

- **4 steps is the native pace.** The model is trained to sample in 4 steps; pushing more is not a quality lever. The engine defaults to 4 and allows up to 20 for experimentation.
- **CFG is fixed at 1.** No guidance, and the graph skips the negative branch entirely (one text-encode pass via `ConditioningZeroOut`).
- **No LoRA.** There is no turbo LoRA to load — the graph is the bare FLUX.2
  custom-sampler pipeline: GGUF loader, CLIP, VAE, positive encode, (zeroed)
  negative, latent, FlowMatch scheduler, noise, CFG guider, euler sampler,
  decode, save.

On CPU the transformer is `flux-2-klein-4b-Q2_K.gguf` (2-bit Q2_K GGUF, ~1.7 GB) — the same distilled FLUX.2-Klein-4B build Bonsai Image 4B is quantized from, loaded by molbal's `UnetLoaderGGUF` (149 tensors, identical layout, arch detected by tensor names). Bonsai's own 1-bit (Q1_0) file cannot be dequantized by any current toolchain — ggml, the `gguf` package, and molbal all lack a Q1_0 decoder (verified: the file's 80 Q1_0 tensors raise `NotImplementedError`) — so the 2-bit quant of the same weights is the smallest loadable form. If arch detection ever rejects the GGUF on this host, the sanctioned fallback is the plain `UNETLoader` with the 7.75 GB fp16 transformer (flip `ANOTHERLAB_UNET_LOADER` / `ANOTHERLAB_UNET` and restart; `remote_models_fallback.sh` fetches the fp16 file).

## Quick start on kube-labs

Prerequisites: the `kube-labs` CLI, authenticated, and `jq` locally.

```bash
# 1. Create an 8-unit machine (CPU only; that is the largest size)
kube-labs vm create --image docker-linux --units 8 --name bonsai --wait

# 2. Provision it: CPU PyTorch, ComfyUI, the GGUF node, and disk cleanup
make setup MACHINE=bonsai

# 3. Download the CPU weights (~5.1 GB)
make models MACHINE=bonsai

# 4. Upload this repo and start both services
make deploy MACHINE=bonsai

# 5. Publish the UI on a Cloudflare quick tunnel and print the URL
make tunnel MACHINE=bonsai
```

The first generation is slow because ComfyUI loads the text encoder and the transformer from disk. The UI says so; subsequent jobs reuse the warm process.

### Manual equivalent

```bash
kube-labs exec bonsai -- bash /srv/anotherlab/scripts/remote_setup.sh
kube-labs exec bonsai -- bash /srv/anotherlab/scripts/remote_models.sh
./scripts/deploy.sh bonsai
./scripts/tunnel.sh bonsai 8080
```

Services are managed by systemd:

```bash
kube-labs exec bonsai -- systemctl status comfyui anotherlab
kube-labs exec bonsai -- journalctl -u anotherlab -n 100
```

## Using the UI

1. Type a prompt (or press **Surprise me**).
2. Optionally add style suffixes, pick an aspect ratio and quality.
3. Press **Generate** (⌘↵ / Ctrl↵).
4. Watch the progress ring; you can close the tab — the job keeps running.
5. Download the PNG before the countdown reaches zero. **Delete now** removes it immediately.

`Draft` (512 px) is the sensible default on CPU. `Balanced` (768 px) and `Detail` (1024 px) scale pixels, and CPU time scales with them.

## Public URL with Cloudflare

`make tunnel MACHINE=bonsai` installs `cloudflared` on the machine, runs it as
`anotherlab-tunnel.service`, and prints the `https://<name>.trycloudflare.com`
URL. It also writes that URL to `ANOTHERLAB_PUBLIC_URL` and restarts the gateway so
generated links and MCP responses use the public host.

- **UI:** https://views-finder-red-cities.trycloudflare.com
- **MCP:** https://views-finder-red-cities.trycloudflare.com/mcp

**Status:** the tunnel above is live as of **2026-11-25** — UI page,
`/api/health`, and an MCP `initialize` call all verified from the public
internet. The engine runs the distilled FLUX.2-Klein-4B build (2-bit Q2_K
GGUF) on the `bonsai` VM.

A quick tunnel needs no Cloudflare account. The hostname is **ephemeral**: it
changes whenever `anotherlab-tunnel` restarts. For a stable hostname, create a
named tunnel with `cloudflared tunnel login` and `cloudflared tunnel create`,
then point its ingress at `http://127.0.0.1:8080`.

The platform's own URL (or a local port-forward) is still available with
`make tunnel-platform MACHINE=bonsai`.

```bash
# On the machine
systemctl status anotherlab-tunnel
journalctl -u anotherlab-tunnel -n 50
```

Set `ANOTHERLAB_API_KEY` before publishing publicly. The quick tunnel is a real
public listener; without a key, anyone with the hostname can generate images.

## Using MCP

The gateway speaks Streamable HTTP at `/mcp`. No SDK is required on the server side.

### Claude Code

```bash
claude mcp add --transport http anotherlab https://<your-machine>/mcp
```

### Generic client config

```json
{
  "mcpServers": {
    "anotherlab": {
      "type": "http",
      "url": "https://<your-machine>/mcp"
    }
  }
}
```

### Stdio-only clients

```bash
python -m gateway.mcp_stdio --url https://<your-machine>/mcp
```

Add `--key <API_KEY>` when the server has `ANOTHERLAB_API_KEY` set.

### Tools

| Tool | Arguments | Returns |
|---|---|---|
| `generate_image` | `prompt`, `width`, `height`, `steps`, `cfg`, `seed`, `negative_prompt`, `wait_seconds` | Job summary + PNG inline when ready |
| `get_generation` | `id` | Your job by id, with live progress while running + PNG inline while alive |
| `cancel_generation` | `id` | Updated status |
| `engine_info` | — | Device, queue, defaults, limits, TTL |

`generate_image` blocks until the engine has the job, then for up to `wait_seconds` (default 90, max 600). On CPU, a job often exceeds that, so it returns `running` (with progress, step and ETA) and the agent polls `get_generation`. There is deliberately no listing tool — agents only ever see the jobs they started; cross-user history stays in the web UI and `GET /api/generations`. The tool text tells the agent to save the bytes immediately, because the URL dies with the file.

**Over a Cloudflare quick tunnel, prefer `wait_seconds: 0`.** Quick tunnels cut off origin requests after 120 s (error 524); the job keeps running server-side, but the agent that blocked has no listing tool to recover the ID. With `wait_seconds: 0` the tool returns the id as soon as the engine has the job (sub-second), and the agent polls `get_generation` at its own pace. A tunnel restart changes the hostname, so a long-blocking call has a second chance to die.

See [docs/MCP.md](docs/MCP.md) for raw JSON-RPC examples and the exact response shape.

## REST API

```bash
curl -s http://127.0.0.1:8080/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a bonsai tree in a black ceramic pot, soft light","width":512,"height":512,"steps":4}'
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Engine status, queue, counts, defaults, limits |
| `GET` | `/api/config` | Public config for the UI |
| `POST` | `/api/generate` | Queue a generation (202) |
| `GET` | `/api/generations` | Recent generations |
| `GET` | `/api/generations/{id}` | One generation |
| `POST` | `/api/generations/{id}/cancel` | Cancel queued/running |
| `DELETE` | `/api/generations/{id}` | Delete the image now |
| `GET` | `/api/images/{id}` | The PNG (410 after expiry) |
| `GET` | `/api/events` | SSE: snapshot, job updates, expiries, status |
| `POST` | `/mcp` | MCP JSON-RPC |

OpenAPI docs live at `/api/docs`.

## Configuration

Copy `.env.example` to `/etc/anotherlab.env` on the machine (the deploy script does this once) and edit it.

| Variable | Default | Meaning |
|---|---|---|
| `COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI endpoint |
| `COMFY_OUTPUT_DIR` | `/srv/ComfyUI/output` | Where ComfyUI writes PNGs |
| `ANOTHERLAB_UNET` | `flux-2-klein-4b-Q2_K.gguf` | Transformer file name |
| `ANOTHERLAB_UNET_LOADER` | `UnetLoaderGGUF` | `UnetLoaderGGUF` (2-bit GGUF) or `UNETLoader` (fp16 fallback) |
| `ANOTHERLAB_CLIP` | `qwen_3_4b_fp4_flux2.safetensors` | Text encoder |
| `ANOTHERLAB_CLIP_TYPE` | `flux2` | ComfyUI CLIP type |
| `ANOTHERLAB_VAE` | `flux2-vae.safetensors` | VAE |
| `ANOTHERLAB_DEVICE_LABEL` | `CPU` | Shown in the UI |
| `ANOTHERLAB_TTL_SECONDS` | `300` | Image lifetime after completion |
| `ANOTHERLAB_JOB_TIMEOUT` | `3600` | Hard per-job timeout |
| `ANOTHERLAB_DEFAULT_WIDTH/HEIGHT` | `512` | Defaults |
| `ANOTHERLAB_DEFAULT_STEPS` | `4` | Defaults (native pace) |
| `ANOTHERLAB_MAX_DIMENSION` | `1024` | Upper bound |
| `ANOTHERLAB_MAX_STEPS` | `20` | Upper bound |
| `ANOTHERLAB_ETA_SCALE` | `1` | ETA calibration multiplier (`0.04` for GPU) |
| `ANOTHERLAB_DATA_DIR` | `/srv/anotherlab/data` | SQLite + images |
| `ANOTHERLAB_API_KEY` | empty | Optional shared secret |
| `ANOTHERLAB_PUBLIC_URL` | empty | Absolute URLs in `/api/config` |

If `ANOTHERLAB_API_KEY` is set, every `/api/*` and `/mcp` request needs `Authorization: Bearer <key>` or `X-API-Key: <key>`. The UI asks once and stores the key in `localStorage`. **Do not expose the machine publicly without a key.**

## How the TTL works

- A successful job gets `expires_at = finished_at + ANOTHERLAB_TTL_SECONDS`.
- The reaper wakes every two seconds, finds due jobs, deletes the file, sets `status='expired'`, clears `filename`, and emits an SSE `expired` event.
- The image endpoint returns `410 Gone` once the file is gone.
- Metadata older than 24 hours is purged.
- Deleting an image early uses the same path, so the UI and agents see a consistent state.
- On restart, interrupted `running` jobs are requeued and orphan PNGs are removed.

The countdown in the UI is computed from `expires_in` plus local elapsed time, so it stays honest even if SSE reconnects.

## Performance

CPU generation is the whole story on kube-labs. With 8 vCPU and the 2-bit Q2_K GGUF transformer:

- 256×256, 4 steps: the fastest useful size; expect the one-time model load (~5 GB) on the first job of a cold process.
- 512×512, 4 steps: the default; the warm-process baseline to calibrate `ANOTHERLAB_ETA_SCALE` against.
- 768×768: ~2.25× the pixels of 512, so noticeably longer.
- 1024×1024: ~4× the pixels of 512. Use it for a final pass, not iteration.

The model is distilled, so **more steps does not buy quality** — 4 is the pace it was trained at. Pushing `steps` above 4 is an experiment, not a recommendation.

The ETA in the UI is a calibrated guess until real jobs complete; after that it uses observed progress. Run `make trial` to measure your machine and adjust `ANOTHERLAB_ETA_SCALE`.

## Development

```bash
make install     # venv + dev dependencies
make test        # 30 tests: graph, store, MCP, engine e2e, API e2e
make run         # gateway on :8080 (expects ComfyUI at :8188)
```

The gateway is deliberately dependency-light: FastAPI, httpx, websockets, SQLite. MCP is implemented directly in `gateway/mcp_http.py` rather than through an SDK, so protocol upgrades cannot break the server.

```
gateway/
  config.py     environment-driven settings
  store.py      SQLite job store (claim, expire, purge)
  workflow.py   ComfyUI graph builder + ETA
  comfy.py      async ComfyUI client (submit, history, WS progress)
  engine.py     queue worker, events, TTL reaper, waiters
  main.py       FastAPI app, REST, SSE, static UI
  mcp_http.py   MCP JSON-RPC over Streamable HTTP
  mcp_stdio.py  stdio bridge for local agents
web/            the UI (no build step)
scripts/        provisioning, deploy, tunnel, trial
profiles/       CPU environment profile
systemd/        comfyui.service, anotherlab.service, anotherlab-tunnel.service
tests/          pytest suite
```

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

**`engine offline` in the UI.** ComfyUI is not up. Check `systemctl status comfyui` and `journalctl -u comfyui -n 100`. The gateway reconnects automatically.

**First job never finishes.** ComfyUI is loading the text encoder and transformer from disk on a CPU box. Watch `journalctl -u comfyui -f`; the first load can take minutes. Subsequent jobs are much faster.

**`ComfyUI rejected the workflow`.** The node names or file names do not match the installed ComfyUI. Confirm the GGUF custom node is present (`custom_nodes/ComfyUI-GGUF`) and that `models/diffusion_models/flux-2-klein-4b-Q2_K.gguf` exists.

**GGUF architecture detection fails.** The GGUF has no `arch` metadata in the Bonsai-style build (`n_kv=0`); the ComfyUI-named file the repo ships sets it, but on a host where tensor-name detection still rejects the build, the job fails at validation. Flip the sanctioned fallback:

```bash
# fetch the fp16 transformer (~7.8 GB; the script is idempotent)
kube-labs exec bonsai -- bash /srv/anotherlab/scripts/remote_models_fallback.sh
# point the gateway at the plain loader
sed -i 's|^ANOTHERLAB_UNET=.*|ANOTHERLAB_UNET=flux-2-klein-4b.safetensors|' /etc/anotherlab.env
sed -i 's|^ANOTHERLAB_UNET_LOADER=.*|ANOTHERLAB_UNET_LOADER=UNETLoader|' /etc/anotherlab.env
systemctl restart anotherlab
```

**MCP call over the tunnel dies with Cloudflare error 524.** Quick tunnels cut origin requests at 120 s. The generation keeps running server-side, but the blocked client has no listing tool to recover the job id. Use `wait_seconds: 0` and poll `get_generation` (see [docs/MCP.md](docs/MCP.md)).

**Out of disk.** `df -h /`. The CPU profile needs ~5.8 GB of weights plus ~2.5 GB for the runtime. The fp16 fallback adds ~7.75 GB more — do not keep both transformers at once.

**Images disappear too fast.** That is the product. Download or copy them before the countdown ends; the TTL is `ANOTHERLAB_TTL_SECONDS`.

## Model and license

Bonsai Image 4B is a quantization-aware-training (QAT) distillation of [FLUX.2-Klein-4B](https://huggingface.co/Comfy-Org/FLux-2-Klein-4B) (the [1-bit GGUF](https://huggingface.co/Green-Sky/bonsai-image-binary-4B-GGUF) is published by [Green-Sky](https://huggingface.co/Green-Sky/bonsai-image-binary-4B-GGUF)); since 1-bit Q1_0 has no dequantizer in any current toolchain, this repo runs the same distilled model as [Q2_K GGUF](https://huggingface.co/unsloth/FLUX.2-klein-4B-GGUF) (149 tensors, identical layout). This repo is just a runner; it does not redistribute weights. Check the model cards for the current license and usage terms.

## Status

- Gateway, UI, MCP server, tests, and deployment scripts are complete.
- `make test` is green locally (30 tests).
- Deployment and the public Cloudflare URL are recorded once the tunnel is up.
