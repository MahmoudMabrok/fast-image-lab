"""Thin async client for the ComfyUI HTTP + websocket API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx
import websockets

log = logging.getLogger("anotherlab.comfy")


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(30.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def system_stats(self) -> dict[str, Any]:
        r = await self._http.get("/system_stats")
        r.raise_for_status()
        return r.json()

    async def queue(self) -> dict[str, Any]:
        r = await self._http.get("/queue")
        r.raise_for_status()
        return r.json()

    async def submit(self, graph: dict[str, Any], client_id: str) -> str:
        r = await self._http.post("/prompt", json={"prompt": graph, "client_id": client_id})
        if r.status_code >= 400:
            detail = r.text
            try:
                payload = r.json()
                if payload.get("node_errors"):
                    detail = json.dumps(payload["node_errors"])[:1200]
                elif payload.get("error"):
                    detail = json.dumps(payload["error"])[:1200]
            except Exception:  # noqa: BLE001
                pass
            raise ComfyError(f"ComfyUI rejected the workflow: {detail}")
        payload = r.json()
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI returned no prompt_id: {payload}")
        return str(prompt_id)

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        r = await self._http.get(f"/history/{prompt_id}")
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        if prompt_id in data:
            return data[prompt_id]
        # Newer builds return the entry directly.
        if "outputs" in data or "status" in data:
            return data
        return None

    async def interrupt(self) -> None:
        try:
            await self._http.post("/interrupt", json={})
        except Exception as exc:  # noqa: BLE001
            log.warning("interrupt failed: %s", exc)

    async def free(self, unload_models: bool = False) -> None:
        try:
            await self._http.post(
                "/free", json={"unload_models": unload_models, "free_memory": True}
            )
        except Exception:  # noqa: BLE001
            pass

    async def watch(
        self,
        client_id: str,
        prompt_id: str,
        on_progress: Callable[[int, int, str | None], Awaitable[None]],
        stop: asyncio.Event,
    ) -> None:
        """Follow execution events until the prompt finishes or `stop` is set."""
        url = f"{self.ws_url}?clientId={client_id}"
        delay = 0.5
        while not stop.is_set():
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024, open_timeout=10) as ws:
                    delay = 0.5
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        if isinstance(raw, bytes):
                            continue
                        msg = json.loads(raw)
                        kind = msg.get("type")
                        data = msg.get("data") or {}
                        if data.get("prompt_id") not in (None, prompt_id):
                            continue
                        if kind == "progress":
                            await on_progress(
                                int(data.get("value", 0)),
                                int(data.get("max", 0)),
                                data.get("node"),
                            )
                        elif kind == "executing":
                            if data.get("node") is None:
                                return
                            await on_progress(0, 0, str(data.get("node")))
                        elif kind in ("execution_error", "execution_interrupted"):
                            return
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("ws reconnect (%s)", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5)


def extract_image(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first SaveImage output from a history entry."""
    for _node_id, output in (entry.get("outputs") or {}).items():
        for image in output.get("images") or []:
            if image.get("type", "output") == "output":
                return image
    return None
