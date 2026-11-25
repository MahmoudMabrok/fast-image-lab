"""Model Context Protocol server over Streamable HTTP.

Implements the JSON-RPC surface agents need (initialize, tools/list,
tools/call, resources/list, resources/read) without an SDK dependency, so the
same module works with Claude Code, Codex, Cursor and custom clients.

Endpoint: POST /mcp
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from .config import Settings
from .engine import Engine
from .store import ACTIVE

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST = PROTOCOL_VERSIONS[-1]


def _tool(name: str, description: str, schema: dict[str, Any], output: dict[str, Any] | None = None) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": schema,
    }
    if output:
        tool["outputSchema"] = output
    return tool


IMAGE_OUTPUT = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "status": {"type": "string"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "seed": {"type": "integer"},
        "steps": {"type": "integer"},
        "expires_at": {"type": ["number", "null"]},
        "expires_in": {"type": ["integer", "null"]},
        "image_url": {"type": ["string", "null"]},
        "elapsed_seconds": {"type": "number"},
    },
}


def build_tools(settings: Settings) -> list[dict[str, Any]]:
    lo, hi = settings.min_dimension, settings.max_dimension
    return [
        _tool(
            "generate_image",
            (
                "Generate an image with Bonsai Image 4B (distilled FLUX.2-Klein-4B, 2-bit Q2_K GGUF). "
                "Images self-destruct "
                f"{settings.ttl_seconds} seconds after they are ready. This call waits for the "
                "result when wait_seconds allows and returns the PNG inline as image content; "
                "call get_generation with the id to keep checking. wait_seconds=0 returns the "
                "id as soon as the engine has the job. This engine is single-worker and "
                f"{settings.device_label}. The model is distilled: CFG is fixed at 1 and the "
                "native pace is 4 steps."
            ),
            {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "What to draw. Natural language.",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "Only used when cfg > 1.",
                    },
                    "width": {
                        "type": "integer",
                        "minimum": lo,
                        "maximum": hi,
                        "multipleOf": 16,
                        "default": settings.default_width,
                    },
                    "height": {
                        "type": "integer",
                        "minimum": lo,
                        "maximum": hi,
                        "multipleOf": 16,
                        "default": settings.default_height,
                    },
                    "steps": {
                        "type": "integer",
                        "minimum": 4,
                        "maximum": settings.max_steps,
                        "default": settings.default_steps,
                    },
                    "cfg": {"type": "number", "minimum": 1, "maximum": 10, "default": 1},
                    "seed": {"type": "integer", "description": "Omit for a random seed."},
                    "wait_seconds": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": settings.mcp_wait_max,
                        "default": settings.mcp_wait_default,
                        "description": "How long to block waiting for the image.",
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            IMAGE_OUTPUT,
        ),
        _tool(
            "get_generation",
            "Check a generation and return the image inline if it is ready and not yet expired.",
            {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            IMAGE_OUTPUT,
        ),
        _tool(
            "cancel_generation",
            "Cancel a queued or running generation.",
            {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "engine_info",
            "Engine status: device, queue, defaults, limits and image lifetime.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
    ]


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class McpServer:
    def __init__(self, settings: Settings, engine: Engine):
        self.settings = settings
        self.engine = engine
        self.tools = build_tools(settings)
        self.sessions: dict[str, float] = {}

    def _image_content(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        path = self.engine.image_path(job)
        if path is None:
            return []
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return [{"type": "image", "data": data, "mimeType": "image/png"}]

    def _summary(self, job: dict[str, Any]) -> dict[str, Any]:
        out = {
            "id": job["id"],
            "status": job["status"],
            "width": job["width"],
            "height": job["height"],
            "seed": job["seed"],
            "steps": job["steps"],
            "expires_at": job.get("expires_at"),
            "expires_in": job.get("expires_in"),
            "image_url": f"/api/images/{job['id']}" if job.get("filename") else None,
            "elapsed_seconds": round(
                (job.get("finished_at") or time.time())
                - (job.get("started_at") or job.get("created_at") or time.time()),
                1,
            ),
        }
        if job["status"] in ACTIVE:
            out["queue_position"] = self.engine.store.queue_position(job["id"])
            out["eta_seconds"] = self.engine._eta(job)
            if job["status"] == "running":
                out["progress"] = round(float(job.get("progress") or 0.0), 3)
                out["step"] = int(job.get("step") or 0)
                out["step_total"] = int(job.get("total_steps") or job["steps"])
        if job["status"] == "failed" and job.get("error"):
            out["error"] = job["error"]
        return out

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "generate_image":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                return self._tool_error("prompt is required")
            job = await self.engine.submit(
                prompt=prompt,
                negative=str(args.get("negative_prompt") or self.settings.default_negative),
                width=int(args.get("width") or self.settings.default_width),
                height=int(args.get("height") or self.settings.default_height),
                steps=int(args.get("steps") or self.settings.default_steps),
                cfg=float(args.get("cfg") or self.settings.default_cfg),
                seed=args.get("seed"),
                source="mcp",
            )
            wait = max(
                0, min(self.settings.mcp_wait_max, int(args.get("wait_seconds", self.settings.mcp_wait_default)))
            )
            # Hold the call until the engine actually has the job so the
            # summary never reports "queued"; 60s caps a hung engine.
            took = time.time()
            while job["status"] == "queued" and time.time() - took < 60:
                job = await self.engine.wait(job["id"], min(5.0, 60.0 - (time.time() - took))) or job
            # Then spend the requested budget waiting for completion.
            remaining = wait - (time.time() - took)
            if remaining > 0 and job["status"] in ACTIVE:
                job = await self.engine.wait(job["id"], remaining) or job
            return self._job_response(job)

        if name == "get_generation":
            job = self.engine.store.get(str(args.get("id") or ""))
            if job is None:
                return self._tool_error("generation not found")
            return self._job_response(job)

        if name == "cancel_generation":
            job = await self.engine.cancel(str(args.get("id") or ""))
            if job is None:
                return self._tool_error("generation not found")
            return {
                "content": [{"type": "text", "text": f"Generation {job['id']} is {job['status']}."}],
                "structuredContent": {"id": job["id"], "status": job["status"]},
                "isError": False,
            }

        if name == "engine_info":
            status = self.engine.status()
            return {
                "content": [{"type": "text", "text": json.dumps(status, indent=2)}],
                "structuredContent": status,
                "isError": False,
            }

        return self._tool_error(f"unknown tool: {name}")

    def _tool_error(self, message: str) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": message}],
            "isError": True,
        }

    def _job_response(self, job: dict[str, Any]) -> dict[str, Any]:
        summary = self._summary(job)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": json.dumps(summary, indent=2)}
        ]
        if job["status"] == "succeeded":
            content += self._image_content(job)
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"The PNG is attached. It is deleted at {time.strftime('%H:%M:%S', time.localtime(job['expires_at']))} "
                        f"({job['expires_in']}s from now); save it now."
                    ),
                }
            )
        elif job["status"] in ACTIVE:
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Still {job['status']} — progress, step and ETA are in the summary above. "
                        f"Call get_generation with id={job['id']} to keep checking."
                    ),
                }
            )
        return {"content": content, "structuredContent": summary, "isError": False}

    async def handle(self, payload: dict[str, Any], session_id: str | None) -> tuple[dict[str, Any] | None, dict[str, str]]:
        headers: dict[str, str] = {}
        method = payload.get("method")
        request_id = payload.get("id")
        params = payload.get("params") or {}

        if method == "initialize":
            sid = session_id or secrets.token_urlsafe(18)
            self.sessions[sid] = time.time()
            headers["Mcp-Session-Id"] = sid
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else LATEST
            return (
                _result(
                    request_id,
                    {
                        "protocolVersion": version,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"listChanged": False, "subscribe": False},
                        },
                        "serverInfo": {"name": "fast-image-lab", "version": "1.0.0"},
                        "instructions": (
                            "Bonsai Image 4B (FLUX.2-Klein) image generation. Call generate_image; "
                            "the PNG is returned inline and is deleted "
                            f"{self.settings.ttl_seconds}s after creation."
                        ),
                    },
                ),
                headers,
            )

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None, headers

        if method == "ping":
            return _result(request_id, {}), headers

        if method == "tools/list":
            return _result(request_id, {"tools": self.tools}), headers

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return _error(request_id, -32602, "arguments must be an object"), headers
            try:
                result = await self.call_tool(str(name), args)
            except ValueError as exc:
                result = self._tool_error(str(exc))
            except Exception as exc:  # noqa: BLE001
                result = self._tool_error(f"{type(exc).__name__}: {exc}")
            return _result(request_id, result), headers

        if method == "resources/list":
            return (
                _result(
                    request_id,
                    {
                        "resources": [
                            {
                                "uri": "fast-image-lab://engine",
                                "name": "Engine status",
                                "mimeType": "application/json",
                            },
                        ]
                    },
                ),
                headers,
            )

        if method == "resources/read":
            uri = str(params.get("uri") or "")
            if uri == "fast-image-lab://engine":
                body = self.engine.status()
            else:
                return _error(request_id, -32602, f"unknown resource: {uri}"), headers
            return (
                _result(
                    request_id,
                    {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(body, indent=2),
                            }
                        ]
                    },
                ),
                headers,
            )

        if request_id is None:
            return None, headers
        return _error(request_id, -32601, f"method not found: {method}"), headers


def build_router(settings: Settings, engine: Engine) -> APIRouter:
    router = APIRouter()
    server = McpServer(settings, engine)

    def authorized(authorization: str | None, api_key: str | None) -> bool:
        if not settings.api_key:
            return True
        if api_key and secrets.compare_digest(api_key, settings.api_key):
            return True
        if authorization and authorization.lower().startswith("bearer "):
            return secrets.compare_digest(authorization[7:], settings.api_key)
        return False

    @router.post("/mcp")
    async def mcp_post(
        request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        mcp_session_id: str | None = Header(default=None),
    ) -> Response:
        if not authorized(authorization, x_api_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                _error(None, -32700, "parse error"), status_code=400
            )

        if isinstance(payload, list):
            responses = []
            headers: dict[str, str] = {}
            for item in payload:
                if not isinstance(item, dict):
                    responses.append(_error(None, -32600, "invalid request"))
                    continue
                result, extra = await server.handle(item, mcp_session_id)
                headers.update(extra)
                if result is not None:
                    responses.append(result)
            if not responses:
                return Response(status_code=202, headers=headers)
            return JSONResponse(responses, headers=headers)

        if not isinstance(payload, dict):
            return JSONResponse(_error(None, -32600, "invalid request"), status_code=400)

        result, headers = await server.handle(payload, mcp_session_id)
        if result is None:
            return Response(status_code=202, headers=headers)
        return JSONResponse(result, headers=headers)

    @router.get("/mcp")
    async def mcp_get() -> Response:
        return Response(
            status_code=405,
            headers={"Allow": "POST, DELETE"},
            content=json.dumps({"error": "use POST for Streamable HTTP"}),
            media_type="application/json",
        )

    @router.delete("/mcp")
    async def mcp_delete(mcp_session_id: str | None = Header(default=None)) -> Response:
        if mcp_session_id:
            server.sessions.pop(mcp_session_id, None)
        return Response(status_code=204)

    return router
