# AGENTS.md

Fast Image Lab is a self-hosted Bonsai Image 4B (FLUX.2-klein) image generation
studio: a FastAPI gateway (queue, SQLite, SSE, TTL reaper, MCP at `/mcp`), a
build-free vanilla JS UI in `web/`, and ComfyUI as the inference engine.
Images self-destruct five hours after completion.

## Task tracking (mandatory)

Every task — code change, bug fix, deploy, investigation, docs — must be run as
a todo checklist, so progress is visible and nothing is silently dropped.

1. **Write the checklist before starting.** Put a markdown task list in the
   reply and mirror it in [`TODO.md`](TODO.md) at the repo root.
2. **Make steps concrete and verifiable.** One line per step, phrased as an
   outcome ("gateway returns `/api/health` 200"), not an activity ("look into").
3. **Check a box only after verifying it** — command run, test passed, file
   inspected, endpoint queried. Never mark a step done based on intention.
4. **Update `TODO.md` as each step completes.** Do not batch the updates at
   the end of the task.
5. **Work until every box is checked.** If a step is blocked, leave it
   unchecked, note the blocker inline, and call it out in the reply.
6. **End every reply with the checklist state**, including anything still open.

Checking a box that was not actually verified is a defect, not a shortcut.

When all items are done, move the list under a `## Completed` heading in
`TODO.md` with the date, so in-flight work stays at the top of the file.

## Project rules

- `make test` (30 pytest tests) must pass before any code change is called
  complete. Add or update tests for behavior changes.
- Keep the gateway dependency-light: FastAPI, httpx, websockets, SQLite. MCP is
  hand-written in `gateway/mcp_http.py`; do not swap it for the `mcp` SDK.
- The UI has no build step. Edit the three files in `web/` directly.
- Images are ephemeral by design. Do not add a persistent gallery or caching
  that outlives `ANOTHERLAB_TTL_SECONDS` without an explicit decision.
- The model is **distilled**: 4 steps, CFG 1, and **no LoRA** anywhere
  (engine, store, MCP schema, UI). Do not re-introduce a LoRA path.
- Bonsai's 1-bit (Q1_0) GGUF **cannot be dequantized by any current
  toolchain** (no Q1_0 decoder in ggml, the `gguf` package, or molbal), so
  the CPU build runs the same distilled FLUX.2-Klein-4B model as
  `flux-2-klein-4b-Q2_K.gguf` (149 tensors, identical layout). The
  sanitized fallback is `ANOTHERLAB_UNET_LOADER=UNETLoader` +
  `ANOTHERLAB_UNET=flux-2-klein-4b.safetensors` (the distilled fp16 file).
  Never keep both transformers on the 16 GB disk at once.
- Remote work targets the `bonsai` kube-labs machine (`make setup/models/
  deploy/tunnel/status`). Never commit secrets or `/etc/anotherlab.env`.
- The public tunnel URL, once captured, is written into `README.md` and
  committed.
- Commit only when asked. Leave unrelated user changes untouched.

## Map

```
gateway/   FastAPI app, queue engine, SQLite store, ComfyUI client, MCP
web/       static UI (index.html, styles.css, app.js)
scripts/   provisioning, deploy, tunnel, trial
profiles/  CPU (kube-labs) env profile
systemd/   comfyui.service, anotherlab.service, anotherlab-tunnel.service
tests/     pytest suite, including a fake ComfyUI
docs/      ARCHITECTURE.md, MCP.md
```
