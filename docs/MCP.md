# MCP

AnotherLab exposes a Model Context Protocol server at `POST /mcp` using Streamable HTTP. It is implemented directly against JSON-RPC, so it works with any client that speaks the protocol.

- Protocol versions: `2024-11-05`, `2025-03-26`, `2025-06-18`
- Transport: HTTP POST, JSON responses (no SSE upgrade required)
- Session: the `initialize` response includes `Mcp-Session-Id`; send it back on later calls
- Auth: if `ANOTHERLAB_API_KEY` is set, send `Authorization: Bearer <key>` or `X-API-Key: <key>`

## Client configuration

### Claude Code

```bash
claude mcp add --transport http anotherlab https://<machine>/mcp
```

With a key:

```bash
claude mcp add --transport http anotherlab https://<machine>/mcp \
  --header "Authorization: Bearer $ANOTHERLAB_API_KEY"
```

### JSON config (Cursor, Codex, others)

```json
{
  "mcpServers": {
    "anotherlab": {
      "type": "http",
      "url": "https://<machine>/mcp",
      "headers": {
        "Authorization": "Bearer <API_KEY>"
      }
    }
  }
}
```

Omit `headers` when the server has no key.

### Stdio-only clients

```bash
python -m gateway.mcp_stdio --url https://<machine>/mcp --key <API_KEY>
```

```json
{
  "mcpServers": {
    "anotherlab": {
      "command": "python",
      "args": ["-m", "gateway.mcp_stdio", "--url", "https://<machine>/mcp"]
    }
  }
}
```

The bridge forwards newline-delimited JSON-RPC from stdin to the HTTP endpoint and prints the response. It keeps the `Mcp-Session-Id` for the lifetime of the process.

## Handshake

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "protocolVersion":"2025-06-18",
  "capabilities":{},
  "clientInfo":{"name":"my-agent","version":"1.0"}
}}
```

Response:

```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2025-06-18",
  "capabilities":{
    "tools":{"listChanged":false},
    "resources":{"listChanged":false,"subscribe":false}
  },
  "serverInfo":{"name":"anotherlab","version":"1.0.0"},
  "instructions":"Bonsai Image 4B (FLUX.2-klein) generation. Call generate_image; the PNG is returned inline and is deleted 300s after creation."
}}
```

Then send `notifications/initialized` (the server answers `202 Accepted` with no body).

## Tools

### `generate_image`

Queue a generation and optionally wait for it.

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
  "name":"generate_image",
  "arguments":{
    "prompt":"A bonsai tree in a black ceramic pot, morning light through a shoji screen",
    "width":512,
    "height":512,
    "steps":4,
    "cfg":1,
    "wait_seconds":120
  }
}}
```

| Argument | Type | Default | Notes |
|---|---|---|---|
| `prompt` | string | required | Natural language |
| `negative_prompt` | string | server default | Only used when `cfg > 1` |
| `width`, `height` | integer | 512 | 256–1024, multiple of 16 |
| `steps` | integer | 4 | 4–20; 4 is the model's native pace |
| `cfg` | number | 1 | 1–10; above 1 costs a second text-encode pass |
| `seed` | integer | random | Omit for a random seed |
| `wait_seconds` | integer | 90 | 0–600; the call first holds until the engine has the job, then spends this budget waiting for completion |

The response always contains a text summary and `structuredContent`. The status is never `queued`: the call returns only once the engine has taken the job, so agents see `running` (with `progress`, `step`, `step_total`, `queue_position`, `eta_seconds`) or a terminal status. With `wait_seconds: 0` the id comes back as soon as the engine has the job.

```json
{
  "content": [
    {"type":"text","text":"{\n  \"id\": \"img_…\",\n  \"status\": \"running\", …\n}"},
    {"type":"text","text":"Still running — progress, step and ETA are in the summary above. Call get_generation with id=img_… to keep checking."}
  ],
  "structuredContent": {
    "id": "img_…",
    "status": "running",
    "width": 512,
    "height": 512,
    "seed": 123456789,
    "steps": 4,
    "expires_at": null,
    "expires_in": null,
    "image_url": null,
    "elapsed_seconds": 3.2,
    "queue_position": 0,
    "eta_seconds": 512,
    "progress": 0.25,
    "step": 1,
    "step_total": 4
  },
  "isError": false
}
```

When the image is ready, the response also carries the PNG:

```json
{
  "content": [
    {"type":"text","text":"{ …summary… }"},
    {"type":"image","data":"iVBORw0KGgo…","mimeType":"image/png"},
    {"type":"text","text":"The PNG is attached. It is deleted at 09:41:12 (287s from now); save it now."}
  ],
  "structuredContent": { "status": "succeeded", "expires_in": 287, "image_url": "/api/images/img_…" },
  "isError": false
}
```

**Agents must persist the bytes immediately.** Both the inline base64 and `image_url` point at a file that the reaper deletes five minutes after completion.

### `get_generation`

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
  "name":"get_generation","arguments":{"id":"img_…"}
}}
```

Returns the same shape as `generate_image`. While the job is still active the summary carries live `progress`, `step`, `step_total`, `queue_position` and `eta_seconds`. If the file expired, `status` is `expired` and no image content is returned.

There is deliberately no tool to list generations. This server is a shared engine: agents can only ever read the jobs they started (by the id `generate_image` returned them). Other users' history is not exposed over MCP — it stays in the web UI and the plain REST `GET /api/generations`.

### `cancel_generation`

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{
  "name":"cancel_generation","arguments":{"id":"img_…"}
}}
```

Cancels a queued or running job. Running jobs also interrupt ComfyUI.

### `engine_info`

No arguments. Returns the full `/api/health` payload: device, ComfyUI version, queue depth, counts, defaults, limits, model names and TTL.

## Resources

| URI | Content |
|---|---|
| `anotherlab://engine` | Engine status JSON |

```json
{"jsonrpc":"2.0","id":6,"method":"resources/read","params":{"uri":"anotherlab://engine"}}
```

## Long-running calls behind a tunnel

Cloudflare quick tunnels time out the origin after **120 s** (HTTP 524). The
generation keeps running server-side, but a client still blocked inside
`generate_image` never learns the job id — and there is no listing tool to
recover it from. So behind a tunnel:

1. Call `generate_image` with `wait_seconds: 0`. It returns the id as soon as
   the engine has the job (sub-second in practice).
2. Poll `get_generation` every 15–30 s until `succeeded`; the summary carries
   progress, step and ETA, so the agent knows the job is alive.
3. Direct (non-tunnel) calls may use the full `wait_seconds` budget.

## Errors

- Unknown tool: `isError: true` with a text explanation.
- Empty prompt: `isError: true`, `"prompt is required"`.
- Unknown job: `isError: true`, `"generation not found"`.
- Unknown method: JSON-RPC error `-32601`.
- Bad params: JSON-RPC error `-32602`.
- Unauthorized: HTTP `401` before JSON-RPC handling.

Tool failures are returned as successful JSON-RPC responses with `isError: true`, which is what MCP expects.

## Agent playbook

1. Call `engine_info` once. On CPU, expect minutes per image.
2. Call `generate_image` with `512×512`, `4` steps, `cfg` `1`, and a `wait_seconds` of 60–120.
3. If the status is `running`, poll `get_generation` every 15–30 seconds; the summary carries live progress and an ETA. Do not start a second job unless you want to wait behind it — the engine is single-worker.
4. On `succeeded`, write the base64 image to disk immediately. Mention the expiry to the user.
5. Use the returned `seed` to reproduce or vary an image.
6. Use `cancel_generation` if the user changes their mind; a CPU job can be long.

## Protocol notes

- `GET /mcp` returns `405`; this server does not offer a standalone SSE stream. POST is the only transport.
- `DELETE /mcp` drops the session and returns `204`.
- Batch requests (a JSON array) are supported.
- Notifications (no `id`) return `202` with no body.
- `ping` returns `{}`.
