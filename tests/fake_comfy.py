"""A tiny stand-in for ComfyUI, used by the end-to-end test.

It implements just enough of the API the gateway uses: system_stats, queue,
prompt, history, interrupt and the progress websocket.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lzJ8WQAAAABJRU5ErkJggg=="
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeComfy:
    def __init__(self, output_dir: Path, *, duration: float = 1.2, steps: int = 4):
        self.output_dir = output_dir
        self.duration = duration
        self.steps = steps
        self.history: dict[str, dict[str, Any]] = {}
        self.connections: dict[str, list[WebSocket]] = {}
        self.interrupted = 0
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/system_stats")
        async def system_stats() -> dict[str, Any]:
            return {
                "system": {
                    "os": "posix",
                    "comfyui_version": "0.3.fake",
                    "python_version": "3.11",
                    "pytorch_version": "2.fake",
                    "argv": [],
                    "ram_total": 16 * 1024**3,
                    "ram_free": 8 * 1024**3,
                },
                "devices": [
                    {
                        "name": "Fake CPU",
                        "type": "cpu",
                        "index": None,
                        "vram_total": 0,
                        "vram_free": 0,
                        "torch_vram_total": 0,
                        "torch_vram_free": 0,
                    }
                ],
            }

        @app.get("/queue")
        async def queue() -> dict[str, Any]:
            return {"queue_running": [], "queue_pending": []}

        @app.post("/prompt")
        async def prompt(payload: dict[str, Any]) -> dict[str, Any]:
            prompt_id = uuid.uuid4().hex
            client_id = str(payload.get("client_id") or "")
            asyncio.create_task(self._run(prompt_id, client_id))
            return {"prompt_id": prompt_id, "number": len(self.history) + 1}

        @app.get("/history/{prompt_id}")
        async def history(prompt_id: str) -> dict[str, Any]:
            entry = self.history.get(prompt_id)
            return {prompt_id: entry} if entry else {}

        @app.post("/interrupt")
        async def interrupt() -> dict[str, bool]:
            self.interrupted += 1
            return {"ok": True}

        @app.post("/free")
        async def free() -> dict[str, bool]:
            return {"ok": True}

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await websocket.accept()
            client_id = websocket.query_params.get("clientId") or ""
            self.connections.setdefault(client_id, []).append(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                with contextlib.suppress(ValueError):
                    self.connections.get(client_id, []).remove(websocket)

        return app

    async def _send(self, client_id: str, message: dict[str, Any]) -> None:
        for socket in list(self.connections.get(client_id, [])):
            with contextlib.suppress(Exception):
                await socket.send_json(message)

    async def _run(self, prompt_id: str, client_id: str) -> None:
        await asyncio.sleep(0.2)
        await self._send(client_id, {"type": "executing", "data": {"node": "8", "prompt_id": prompt_id}})
        for step in range(1, self.steps + 1):
            await asyncio.sleep(self.duration / self.steps)
            await self._send(
                client_id,
                {
                    "type": "progress",
                    "data": {
                        "value": step,
                        "max": self.steps,
                        "node": "8",
                        "prompt_id": prompt_id,
                    },
                },
            )
        filename = f"anotherlab_{prompt_id[:8]}_.png"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / filename).write_bytes(PNG_1X1)
        self.history[prompt_id] = {
            "prompt": [0, prompt_id, {}, {}, []],
            "outputs": {"10": {"images": [{"filename": filename, "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
        await self._send(client_id, {"type": "executing", "data": {"node": None, "prompt_id": prompt_id}})


class FakeComfyServer:
    """Run FakeComfy on a background thread."""

    def __init__(self, output_dir: Path, **kwargs: Any):
        self.comfy = FakeComfy(output_dir, **kwargs)
        self.port = free_port()
        config = uvicorn.Config(
            self.comfy.app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "FakeComfyServer":
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not self.server.started:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("fake ComfyUI did not start")
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
