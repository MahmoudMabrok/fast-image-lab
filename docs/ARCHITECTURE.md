# Architecture

AnotherLab is two processes and one static directory.

```
systemd: comfyui.service     systemd: anotherlab.service + anotherlab-tunnel.service
┌──────────────────────┐     ┌──────────────────────────────────────┐
│ ComfyUI :8188        │◄────│ FastAPI :8080                        │
│ headless, CPU        │ HTTP│ queue worker · SQLite · SSE · reaper │
│ Bonsai 4B + TE + VAE │  WS │ REST · /mcp · web/                   │
└──────────────────────┘     └──────────────────────────────────────┘
```

The split keeps the heavy, crash-prone part (model loading, sampling) away from the product surface. If ComfyUI dies, the gateway stays up, reports `engine offline`, and recovers when systemd restarts the engine.

## Why this shape

**ComfyUI as an engine, not as the UI.** ComfyUI already knows how to load the FLUX.2-klein transformer, run the GGUF loader, and decode FLUX.2 latents. Reimplementing that in a custom inference server would mean tracking upstream node changes forever. The gateway talks to ComfyUI's documented HTTP + WebSocket API instead.

**A separate gateway.** ComfyUI's queue is per-process and its UI is a node graph. The product needs job IDs that survive reconnects, a TTL, a clean REST surface, SSE, and MCP. Those are gateway concerns.

**Hand-written MCP.** The `mcp` Python SDK changes shape between releases. The protocol is small: JSON-RPC over HTTP with a session header. `gateway/mcp_http.py` implements the subset agents use (`initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `ping`) and works with Claude Code, Codex, Cursor and raw clients. No version pinning, no transport adapter.

**SQLite.** One writer, a handful of rows per minute, and a need for atomic claim/expire. SQLite in WAL mode is exactly right; a queue service would be more moving parts than the workload justifies.

## Job lifecycle

```
POST /api/generate
        │
        ▼
  store.create(status=queued) ──► SSE {"type":"job"}
        │
        ▼  worker: store.claim_next()  (atomic UPDATE … WHERE status='queued')
  status=running, started_at=now
        │
        ├── build graph (gateway/workflow.py)
        ├── POST /prompt  ──► prompt_id
        ├── WS /ws?clientId=…  ──► progress events ──► SSE
        └── poll /history/{prompt_id} until the entry appears
        │
        ▼
  copy PNG from ComfyUI output to data/images/{id}.png
  delete the ComfyUI copy
  status=succeeded, expires_at=now+TTL ──► SSE {"type":"job"}
        │
        ▼  reaper (every 2s)
  expires_at <= now ──► unlink file, status=expired ──► SSE {"type":"expired"}
```

Only one worker runs by design. A CPU box cannot sample two images at once without making both slower; serializing gives predictable latency and a simple queue position. On a GPU host you would raise the worker count, which is a small change to `Engine._worker_loop`.

### Progress and ETA

The WebSocket `progress` event gives `value`/`max` for the current sampler step. The engine clamps it to `[0.02, 0.99]` so the UI never shows 100% before the PNG exists, and derives an ETA from elapsed time divided by progress. Before any progress arrives, the ETA comes from `estimated_seconds()` in `workflow.py`, calibrated for 8 vCPU and scaled by `ANOTHERLAB_ETA_SCALE`.

The worker also polls `/history` once a second. The WebSocket is an optimization for progress, not the source of truth: if it disconnects, the job still completes and the poll notices.

### Cancellation

- Queued: mark `cancelled` in SQLite; the worker never claims it.
- Running: mark `cancelled`, call ComfyUI `/interrupt`, stop the watcher. The worker loop notices the status change and returns without writing an image.
- Either way, a waiter blocked in `engine.wait()` is released immediately.

### Restart recovery

On startup the engine:

1. calls ComfyUI `/interrupt` to clear a job left running by the previous process,
2. flips any `running` rows back to `queued` (their `prompt_id` is stale),
3. deletes PNGs in `data/images` that no row references.

Completed images keep their original `expires_at` across a restart, so the TTL is wall-clock, not uptime.

## The ComfyUI graph

```
UnetLoaderGGUF ────────────────────────────────────────┐
                                                        ├─► CFGGuider ─┐
CLIPLoader (flux2) ──► CLIPTextEncode ──► positive     │              ├──► SamplerCustomAdvanced
ConditioningZeroOut (cfg ≤ 1.01) ──► negative ─────────┤              │
KSamplerSelect (euler) ────────────────────────────────┤              │
Flux2Scheduler (FlowMatch) ────────────────────────────┤              │
RandomNoise ───────────────────────────────────────────┤              │
EmptyFlux2LatentImage ─────────────────────────────────┘              │
                                                                      ▼
                                       VAEDecode ──► SaveImage
VAELoader ───────────────────────────────────────► VAEDecode
```

There is no LoRA node. Bonsai Image 4B is a distillation of FLUX.2-Klein-4B: the guidance/step distillation is baked into the weights. FLUX.2 does not use the FLUX.1 `KSampler` + scheduler pair — it samples from a fixed FlowMatch schedule — so the graph mirrors the official "Text to Image (FLUX.2 Klein 4B Distilled)" template: `Flux2Scheduler` produces the sigmas, `RandomNoise` the noise, `CFGGuider` carries the single conditioning, and `SamplerCustomAdvanced` runs the euler integrator.

Two details matter on CPU:

**CFG 1 skips the negative branch.** Bonsai is distilled for guidance-free sampling; `cfg=1` is the native setting. At CFG 1 the negative conditioning is never evaluated, but a naive graph still runs the 3.85 GB text encoder twice. The builder uses `ConditioningZeroOut` on the positive conditioning instead of a second `CLIPTextEncode`, so the encoder runs once. When `cfg > 1.01`, it emits a real negative `CLIPTextEncode` and passes the user's negative prompt.

**The Q2_K GGUF is the CPU path.** The transformer ships as `flux-2-klein-4b-Q2_K.gguf` (~1.7 GB) loaded by `UnetLoaderGGUF` from `molbal/ComfyUI-GGUF`. It is the distilled FLUX.2-Klein-4B — the exact build Bonsai Image 4B quantizes from — packed as a 2-bit Q2_K GGUF (149 tensors, identical layout to the Bonsai file). Bonsai's own 1-bit (Q1_0) file is unloadable: no current toolchain has a Q1_0 dequantizer (not ggml, not the `gguf` package, not molbal). One remaining risk: the GGUF has no architecture metadata (`n_kv=0`), so the loader infers the build from tensor names. If that detection rejects the file on a host, the sanctioned fallback is the plain `UNETLoader` with the 7.75 GB fp16 `flux-2-klein-4b.safetensors` (the distilled file, matching Bonsai) — one environment variable (`ANOTHERLAB_UNET_LOADER`) plus `ANOTHERLAB_UNET`, and a restart.

Dimensions are clamped to `[min_dimension, max_dimension]` and rounded down to a multiple of 16, which the FLUX.2 latent expects.

## Storage layout

```
/srv/anotherlab/
  gateway/            code
  web/                static UI
  data/
    anotherlab.sqlite3  job metadata
    images/{id}.png   the only copies; deleted by the reaper
/srv/ComfyUI/
  output/anotherlab*.png  transient; deleted after each copy and after an hour
  models/…
```

The gateway copies the ComfyUI output into `data/images` and immediately deletes the ComfyUI copy, so there is exactly one live copy of each image. The reaper also sweeps `anotherlab*.png` older than an hour in case a copy failed midway.

## TTL and deletion

`expires_at` is set once, on success: `finished_at + ANOTHERLAB_TTL_SECONDS` (300 by default). The reaper runs every two seconds:

```python
for job in store.expire_due():          # UPDATE … WHERE expires_at <= now
    unlink(data/images/{job.filename})
    publish({"type": "expired", "id": job.id})
```

`store.expire_due` selects and flips rows in one transaction, so two reapers cannot both delete the same file. `DELETE /api/generations/{id}` uses the same deletion path. Metadata is purged after 24 hours.

The UI never trusts the server clock alone: it receives `expires_in` and counts down locally, then treats zero as expired even if SSE is disconnected.

## Events

`GET /api/events` is a Server-Sent Events stream:

- on connect: a `snapshot` with engine status, the last 24 jobs, and the server time;
- `job`: any create/update, including progress;
- `expired`: an image was deleted;
- `status`: engine probe result every five seconds;
- a `: ping` comment every 15 seconds to keep proxies from closing the stream.

Subscribers are in-memory `asyncio.Queue`s with a bounded size; a slow client drops events rather than backpressuring the engine. The UI falls back to polling `/api/generations` if SSE fails repeatedly.

## Security model

- No authentication by default, because the intended deployment is a private machine behind a tunnel.
- Set `ANOTHERLAB_API_KEY` to require `Authorization: Bearer …` or `X-API-Key` on `/api/*` and `/mcp`. The UI prompts once and stores the key locally.
- The image endpoint is under the same middleware, so a key protects the bytes as well as the metadata.
- CORS defaults to `*` for convenience; tighten `ANOTHERLAB_CORS_ORIGINS` for a shared deployment.
- Prompts and images are stored on the machine only. There is no telemetry and no outbound call except to ComfyUI on localhost.

## Publishing

`make tunnel` installs `cloudflared` on the machine and runs it as
`anotherlab-tunnel.service` against `http://127.0.0.1:8080`. A quick tunnel needs
no Cloudflare account, so the gateway never has to expose port 8080 or hold a
Cloudflare credential. The hostname is ephemeral: it changes when the tunnel
restarts, which is why the installer writes the current URL into
`ANOTHERLAB_PUBLIC_URL` and restarts the gateway.

The tunnel is a real public listener. With no `ANOTHERLAB_API_KEY`, anyone who
learns the hostname can use the API, MCP tools and image bytes. Set a key before
sharing the URL, or use a named tunnel with an access policy and a stable
hostname.

## Failure modes

| Symptom | Cause | Behavior |
|---|---|---|
| `engine offline` | ComfyUI down | Jobs stay queued; probe retries every 5s |
| Job stuck at 0% | model loading | Expected on first CPU job; timeout is 1h |
| `ComfyUI rejected the workflow` | missing node/file | Job fails with the node error text |
| GGUF arch detection fails | loader can't map tensor names | Job fails at graph validation; flip `ANOTHERLAB_UNET_LOADER=UNETLoader` + `ANOTHERLAB_UNET=flux-2-klein-4b.safetensors` and restart |
| WS drops | proxy/idle | Polling still completes the job |
| Gateway restarts | deploy/crash | Running jobs requeued; images keep their TTL |
| Disk full | too many images | TTL bounds live images to ~5 min of output |

The gateway is intentionally forgiving: a failed job records the exception and stays visible in the UI with a retry button. It never blocks the queue.

## Disk math for kube-labs

The 8-unit machine has 16 GB of disk and ~12 GB free before setup. The CPU profile is sized to fit:

```
transformer  Q2_K GGUF            1.7 GB
text encoder Qwen3-4B fp4         3.9 GB
VAE                               0.3 GB
                               ------
weights                         ~5.9 GB
ComfyUI + PyTorch + deps      ~2.5 GB
```

The quantized model is what makes the build fit at all: the fp16 transformer
alone would be 7.75 GB, and with the text encoder the weights total ~12.3 GB,
leaving almost no room for the runtime on a 16 GB disk. That is why the
fallback path (`remote_models_fallback.sh`) swaps the GGUF for the fp16 file
rather than keeping both at once.

## Extending

- **More workers (GPU):** spawn N `_worker_loop` tasks; the store's `claim_next` is already atomic.
- **More models:** add a profile module that returns a different graph; the engine only needs `build_workflow` and file names.
- **Persistent gallery:** raise `ANOTHERLAB_TTL_SECONDS` and add object storage; the deletion path is a single function.
- **Auth:** replace the shared key with an OIDC proxy in front of the gateway; the middleware already centralizes the check.
