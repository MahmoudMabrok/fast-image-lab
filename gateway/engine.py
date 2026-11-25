"""Job queue, ComfyUI orchestration, and the image TTL reaper."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .comfy import ComfyClient, ComfyError, extract_image
from .config import Settings
from .store import ACTIVE, Store
from .workflow import build_workflow, estimated_seconds, render_prompt

log = logging.getLogger("anotherlab.engine")


class Engine:
    def __init__(self, settings: Settings, store: Store, prompt_vanish_seconds: float = 60.0):
        self.settings = settings
        self.store = store
        self.comfy = ComfyClient(settings.comfy_url)
        self.ready = False
        self.last_error: str | None = None
        self.info: dict[str, Any] = {}
        self.prompt_vanish_seconds = prompt_vanish_seconds
        self._subs: set[asyncio.Queue] = set()
        self._waiters: dict[str, asyncio.Event] = {}
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        self.settings.ensure_dirs()
        await self._recover()
        self._tasks = [
            asyncio.create_task(self._worker_loop(), name="worker"),
            asyncio.create_task(self._reaper_loop(), name="reaper"),
            asyncio.create_task(self._probe_loop(), name="probe"),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.comfy.close()

    async def _recover(self) -> None:
        """Requeue jobs interrupted by a restart and drop orphan images."""
        await self.comfy.interrupt()
        with contextlib.suppress(Exception):
            running = [j for j in self.store.list(200) if j["status"] == "running"]
            for job in running:
                self.store.update(
                    job["id"], status="queued", progress=0, step=0, started_at=None,
                    prompt_id=None, error=None,
                )
        referenced = {
            j["filename"] for j in self.store.list(500) if j.get("filename")
        }
        for path in self.settings.images_dir.glob("*.png"):
            if path.name not in referenced:
                with contextlib.suppress(OSError):
                    path.unlink()
        log.info("recovered: %d queued jobs, %d images on disk", len(self.store.active()), len(referenced))

    # -- events ---------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subs):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        self._subs.add(queue)
        try:
            yield queue
        finally:
            self._subs.discard(queue)

    def _finish_waiter(self, job_id: str) -> None:
        event = self._waiters.pop(job_id, None)
        if event is not None:
            event.set()

    async def wait(self, job_id: str, timeout: float) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        if job["status"] not in ACTIVE:
            return job
        event = self._waiters.setdefault(job_id, asyncio.Event())
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)
        return self.store.get(job_id)

    # -- public API -----------------------------------------------------
    def public_job(self, job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        out = dict(job)
        out.pop("prompt_id", None)
        out["image_url"] = f"/api/images/{job['id']}" if job.get("filename") else None
        if job["status"] in ACTIVE:
            out["queue_position"] = self.store.queue_position(job["id"])
            out["eta_seconds"] = self._eta(job)
        else:
            out["queue_position"] = 0
            out["eta_seconds"] = 0
        return out

    def _eta(self, job: dict[str, Any]) -> int:
        if job["status"] == "queued":
            running_seconds = 0.0
            ahead_seconds = 0.0
            for other in self.store.active():
                if other["id"] == job["id"]:
                    continue
                if other["status"] == "queued":
                    ahead_seconds += estimated_seconds(
                        self.settings, other["width"], other["height"], other["steps"]
                    )
                    continue
                running_start = other.get("started_at") or time.time()
                if other.get("progress") and other["progress"] > 0.03:
                    base = (time.time() - running_start) / other["progress"]
                else:
                    base = estimated_seconds(
                        self.settings, other["width"], other["height"], other["steps"]
                    )
                running_seconds = max(0.0, time.time() - running_start + base * (1 - other["progress"]))
            return int(min(3600, ahead_seconds + running_seconds))
        elapsed = max(0.0, time.time() - (job.get("started_at") or time.time()))
        progress = float(job.get("progress") or 0)
        if progress > 0.03:
            total = elapsed / progress
            return max(0, int(total - elapsed))
        base = estimated_seconds(self.settings, job["width"], job["height"], job["steps"])
        return max(1, int(base - elapsed))

    async def submit(
        self,
        *,
        prompt: str,
        negative: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        seed: int | None,
        source: str = "web",
        styles: str = "",
        quality: str | None = None,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > self.settings.max_prompt_chars:
            raise ValueError(f"prompt is longer than {self.settings.max_prompt_chars} characters")
        if seed is None or int(seed) < 0:
            seed = secrets.randbelow(2**32)
        job = {
            "id": "img_" + secrets.token_urlsafe(9),
            "prompt": prompt,
            "negative": (negative or "").strip(),
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "cfg": float(cfg),
            "seed": int(seed),
            "status": "queued",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "expires_at": None,
            "progress": 0.0,
            "step": 0,
            "total_steps": int(steps),
            "error": None,
            "filename": None,
            "bytes": None,
            "prompt_id": None,
            "source": source,
            "styles": (styles or "").strip(),
            "quality": (quality or "").strip() or None,
        }
        job = self.store.create(job)
        self.publish({"type": "job", "job": self.public_job(job)})
        return job

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        if job["status"] == "queued":
            job = self.store.update(
                job_id, status="cancelled", finished_at=time.time(), progress=0
            )
            self._finish_waiter(job_id)
        elif job["status"] == "running":
            self.store.update(job_id, status="cancelled", finished_at=time.time())
            await self.comfy.interrupt()
            self._finish_waiter(job_id)
        else:
            return job
        job = self.store.get(job_id)
        self.publish({"type": "job", "job": self.public_job(job)})
        return job

    def delete_image(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        # Only jobs that produced a file can be deleted; a running job has no
        # image yet, and an "expired" event for it would confuse the UI.
        if not job.get("filename"):
            return job
        path = self.settings.images_dir / job["filename"]
        with contextlib.suppress(OSError):
            path.unlink()
        job = self.store.update(
            job_id,
            status="expired" if job["status"] == "succeeded" else job["status"],
            filename=None,
            bytes=None,
        )
        self.publish({"type": "expired", "id": job_id})
        return job

    def image_path(self, job: dict[str, Any]) -> Path | None:
        if not job.get("filename"):
            return None
        path = self.settings.images_dir / job["filename"]
        return path if path.exists() else None

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "error": self.last_error,
            "device": self.info.get("device"),
            "device_type": self.info.get("device_type"),
            "comfy_version": self.info.get("comfy_version"),
            "queue_running": self.info.get("queue_running", 0),
            "queue_pending": self.info.get("queue_pending", 0),
            "counts": self.store.counts(),
            "stats": self.store.stats(),
            "ttl_seconds": self.settings.ttl_seconds,
            "defaults": {
                "width": self.settings.default_width,
                "height": self.settings.default_height,
                "steps": self.settings.default_steps,
                "cfg": self.settings.default_cfg,
                "negative": self.settings.default_negative,
            },
            "limits": {
                "min_dimension": self.settings.min_dimension,
                "max_dimension": self.settings.max_dimension,
                "max_steps": self.settings.max_steps,
                "max_prompt_chars": self.settings.max_prompt_chars,
            },
            "model": {
                "name": self.settings.model_name,
                "base": self.settings.model_base,
                "unet": self.settings.unet_name,
                "clip": self.settings.clip_name,
                "vae": self.settings.vae_name,
                "sampler": self.settings.sampler,
                "device_label": self.settings.device_label,
            },
        }

    # -- background loops ----------------------------------------------
    async def _probe_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                stats = await self.comfy.system_stats()
                devices = stats.get("devices") or [{}]
                device = devices[0]
                queue = await self.comfy.queue()
                self.info = {
                    "device": device.get("name") or "unknown",
                    "device_type": device.get("type") or "cpu",
                    "comfy_version": (stats.get("system") or {}).get("comfyui_version"),
                    "queue_running": len(queue.get("queue_running") or []),
                    "queue_pending": len(queue.get("queue_pending") or []),
                }
                self.ready = True
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.ready = False
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.publish({"type": "status", "status": self.status()})
            await asyncio.sleep(5)

    async def _worker_loop(self) -> None:
        while not self._stopping.is_set():
            job = await asyncio.to_thread(self.store.claim_next)
            if job is None:
                await asyncio.sleep(0.4)
                continue
            await self._run_job(job)

    async def _run_job(self, job: dict[str, Any]) -> None:
        settings = self.settings
        job_id = job["id"]
        # The stored prompt is the idea; the saved style presets are folded in
        # here so the text encoder sees the look the user picked.
        graph = build_workflow(
            settings,
            prompt=render_prompt(job["prompt"], job.get("styles") or ""),
            negative=job["negative"],
            width=job["width"],
            height=job["height"],
            steps=job["steps"],
            cfg=job["cfg"],
            seed=job["seed"],
        )
        client_id = uuid.uuid4().hex
        stop = asyncio.Event()
        watcher: asyncio.Task | None = None
        log.info("start %s %sx%s steps=%s", job_id, job["width"], job["height"], job["steps"])
        try:
            prompt_id = await self.comfy.submit(graph, client_id)
            job = self.store.update(job_id, prompt_id=prompt_id, progress=0.02)
            self.publish({"type": "job", "job": self.public_job(job)})

            async def on_progress(value: int, total: int, _node: str | None) -> None:
                if total > 0:
                    progress = max(0.02, min(0.99, value / total))
                    updated = self.store.update(
                        job_id, progress=progress, step=value, total_steps=total
                    )
                else:
                    updated = self.store.update(job_id, progress=0.05)
                self.publish({"type": "job", "job": self.public_job(updated)})

            watcher = asyncio.create_task(
                self.comfy.watch(client_id, prompt_id, on_progress, stop)
            )

            # A prompt accepted by ComfyUI can still die without a history
            # entry — proved live: a gateway restart interrupted the queue,
            # re-queued the job and resubmitted, and the interrupt killed the
            # fresh prompt (before or right after it hit the queue). The old
            # behavior then spun this loop for the full job timeout, jamming
            # the single worker. Rule: a healthy prompt is in the queue or in
            # history within seconds, so 60 s of being in neither means it
            # was lost; fail the job fast and free the worker.
            first_seen = time.time()
            last_seen = first_seen
            seen_in_queue = False
            deadline = time.time() + settings.job_timeout_seconds
            entry: dict[str, Any] | None = None
            while time.time() < deadline:
                current = self.store.get(job_id)
                if current and current["status"] == "cancelled":
                    await self.comfy.interrupt()
                    log.info("cancelled %s", job_id)
                    return
                entry = await self.comfy.history(prompt_id)
                if entry is not None:
                    break
                try:
                    queue = await self.comfy.queue()
                    ids = {p[1] for p in queue.get("queue_running", [])}
                    ids.update(p[1] for p in queue.get("queue_pending", []))
                except Exception:  # noqa: BLE001
                    ids = set()
                if prompt_id in ids:
                    seen_in_queue = True
                    last_seen = time.time()
                elif time.time() - (last_seen if seen_in_queue else first_seen) > self.prompt_vanish_seconds:
                    raise ComfyError(
                        "ComfyUI lost the workflow: it is in neither queue nor history "
                        "after %ds with no output (killed, e.g. by an interrupt); "
                        "nothing was generated" % int(self.prompt_vanish_seconds)
                    )
                await asyncio.sleep(1.0)

            if entry is None:
                raise TimeoutError(
                    f"generation exceeded {settings.job_timeout_seconds}s"
                )

            state = entry.get("status") or {}
            if state.get("status_str") == "error":
                messages = state.get("messages") or []
                raise ComfyError(_error_from_messages(messages))

            image = extract_image(entry)
            if image is None:
                raise ComfyError("ComfyUI finished without an image output")

            src = settings.comfy_output_dir / image.get("subfolder", "") / image["filename"]
            if not src.exists():
                raise ComfyError(f"output file missing: {src}")
            dest = settings.images_dir / f"{job_id}.png"
            await asyncio.to_thread(shutil.copyfile, src, dest)
            with contextlib.suppress(OSError):
                src.unlink()

            now = time.time()
            job = self.store.update(
                job_id,
                status="succeeded",
                progress=1.0,
                step=job["steps"],
                total_steps=job["steps"],
                finished_at=now,
                expires_at=now + settings.ttl_seconds,
                filename=dest.name,
                bytes=dest.stat().st_size,
                error=None,
            )
            log.info("done %s in %.1fs", job_id, now - (job.get("started_at") or now))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("failed %s: %s", job_id, exc)
            job = self.store.update(
                job_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:600],
                finished_at=time.time(),
            )
        finally:
            stop.set()
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watcher
            latest = self.store.get(job_id)
            if latest and latest["status"] in ACTIVE:
                latest = self.store.update(
                    job_id, status="failed", error="worker stopped", finished_at=time.time()
                )
            self.publish({"type": "job", "job": self.public_job(latest)})
            self._finish_waiter(job_id)

    async def _reaper_loop(self) -> None:
        last_purge = 0.0
        while not self._stopping.is_set():
            try:
                due = self.store.expire_due()
                for job in due:
                    path = self.settings.images_dir / (job.get("filename") or "")
                    if job.get("filename"):
                        with contextlib.suppress(OSError):
                            path.unlink()
                    log.info("expired %s", job["id"])
                    self.publish({"type": "expired", "id": job["id"]})

                now = time.time()
                if now - last_purge > 60:
                    last_purge = now
                    self.store.purge_older_than(now - self.settings.history_keep_seconds)
                    self._clean_comfy_output()
                    self._clean_orphans()
            except Exception as exc:  # noqa: BLE001
                log.warning("reaper: %s", exc)
            await asyncio.sleep(2)

    def _clean_comfy_output(self) -> None:
        cutoff = time.time() - 3600
        directory = self.settings.comfy_output_dir
        if not directory.exists():
            return
        for path in directory.glob("anotherlab*.png"):
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()

    def _clean_orphans(self) -> None:
        referenced = {j["filename"] for j in self.store.list(500) if j.get("filename")}
        for path in self.settings.images_dir.glob("*.png"):
            if path.name not in referenced:
                with contextlib.suppress(OSError):
                    path.unlink()


def _error_from_messages(messages: list[Any]) -> str:
    for item in messages:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        kind, data = item[0], item[1]
        if kind in ("execution_error", "execution_interrupted") and isinstance(data, dict):
            parts = [
                str(data.get("exception_type") or ""),
                str(data.get("exception_message") or ""),
            ]
            text = " ".join(p for p in parts if p).strip()
            if text:
                return text
    return "ComfyUI reported an execution error"
