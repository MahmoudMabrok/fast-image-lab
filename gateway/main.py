"""AnotherLab gateway: REST API, live events, static UI and MCP in one process."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .engine import Engine
from .mcp_http import build_router
from .store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("anotherlab")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
settings.ensure_dirs()
store = Store(settings.db_path)
engine = Engine(settings, store)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("starting engine against %s", settings.comfy_url)
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()
        store.close()


app = FastAPI(
    title="AnotherLab",
    description="Bonsai Image 4B (FLUX.2-Klein) image generation with self-destructing images and MCP access.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not settings.api_key:
        return await call_next(request)
    path = request.url.path
    if not (path.startswith("/api") or path.startswith("/mcp")):
        return await call_next(request)
    supplied = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        supplied = auth[7:]
    if not supplied:
        supplied = request.query_params.get("key") or ""
    if supplied != settings.api_key:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return engine.status()


@app.get("/api/config")
async def config() -> dict[str, Any]:
    base = settings.public_base()
    return {
        "ttl_seconds": settings.ttl_seconds,
        "public_url": base,
        "mcp": {
            "http": f"{base}/mcp" if base else "/mcp",
            "name": "anotherlab",
        },
        "device_label": settings.device_label,
        "auth_required": bool(settings.api_key),
    }


@app.post("/api/generate", status_code=202)
async def generate(body: GenerateRequest) -> dict[str, Any]:
    try:
        job = await engine.submit(
            prompt=body.prompt,
            negative=body.negative_prompt if body.negative_prompt is not None else settings.default_negative,
            width=body.width or settings.default_width,
            height=body.height or settings.default_height,
            steps=body.steps or settings.default_steps,
            cfg=body.cfg if body.cfg is not None else settings.default_cfg,
            seed=body.seed,
            source="web",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return engine.public_job(job) or {}


@app.post("/api/warmup", status_code=202)
async def warmup() -> dict[str, Any]:
    job = await engine.submit(
        prompt="a simple grey studio backdrop, soft light",
        negative=settings.default_negative,
        width=settings.min_dimension,
        height=settings.min_dimension,
        steps=4,
        cfg=1.0,
        seed=1,
        source="warmup",
    )
    return engine.public_job(job) or {}


@app.get("/api/generations")
async def generations(limit: int = Query(default=24, ge=1, le=100)) -> dict[str, Any]:
    jobs = [engine.public_job(j) for j in store.list(limit)]
    return {"generations": jobs, "status": engine.status()}


@app.get("/api/generations/{job_id}")
async def generation(job_id: str) -> dict[str, Any]:
    job = engine.public_job(store.get(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return job


@app.post("/api/generations/{job_id}/cancel")
async def cancel(job_id: str) -> dict[str, Any]:
    job = await engine.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return engine.public_job(job) or {}


@app.delete("/api/generations/{job_id}")
async def delete(job_id: str) -> dict[str, Any]:
    job = engine.delete_image(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="generation not found")
    if not job.get("filename") and job["status"] != "expired":
        raise HTTPException(status_code=409, detail="nothing to delete yet")
    return {"id": job_id, "status": "expired"}


@app.get("/api/images/{job_id}")
async def image(job_id: str, download: bool = False) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="image not found")
    path = engine.image_path(job)
    if path is None:
        raise HTTPException(status_code=410, detail="image expired")
    headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="anotherlab-{job_id}.png"'
    return FileResponse(path, media_type="image/png", headers=headers)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        snapshot = {
            "type": "snapshot",
            "status": engine.status(),
            "jobs": [engine.public_job(j) for j in store.list(24)],
            "server_time": time.time(),
        }
        yield _sse(snapshot)
        async with engine.subscribe() as queue:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


app.include_router(build_router(settings, engine))

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:  # pragma: no cover
    @app.get("/")
    async def missing_ui() -> dict[str, str]:
        return {"error": "web/ not found"}


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error: %s", exc)
    return JSONResponse({"detail": "internal error"}, status_code=500)


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "gateway.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
