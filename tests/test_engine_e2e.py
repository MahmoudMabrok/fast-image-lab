"""End-to-end engine test against a fake ComfyUI: submit, progress, copy, expire."""

from __future__ import annotations

import asyncio
import dataclasses
import time

import pytest

from gateway.config import Settings
from gateway.engine import Engine
from gateway.store import Store

from .fake_comfy import FakeComfyServer


def make_settings(tmp_path, comfy_url: str) -> Settings:
    return dataclasses.replace(
        Settings(),
        comfy_url=comfy_url,
        comfy_output_dir=tmp_path / "comfy-output",
        data_dir=tmp_path / "data",
        ttl_seconds=2,
        job_timeout_seconds=30,
        default_width=512,
        default_height=512,
        default_steps=4,
    )


async def test_generation_completes_and_then_self_destructs(tmp_path):
    (tmp_path / "comfy-output").mkdir()
    with FakeComfyServer(tmp_path / "comfy-output", duration=0.8) as fake:
        settings = make_settings(tmp_path, fake.url)
        store = Store(settings.db_path)
        engine = Engine(settings, store)
        await engine.start()
        try:
            job = await engine.submit(
                prompt="a cat",
                negative="",
                width=512,
                height=512,
                steps=4,
                cfg=1.0,
                seed=7,
            )
            done = await engine.wait(job["id"], timeout=20)
            assert done is not None
            assert done["status"] == "succeeded", done.get("error")
            assert done["filename"] == f"{job['id']}.png"
            assert done["expires_at"] > time.time()
            path = settings.images_dir / done["filename"]
            assert path.exists()
            assert path.read_bytes().startswith(b"\x89PNG")
            # The ComfyUI copy is removed after the gateway copies it.
            assert not list((tmp_path / "comfy-output").glob("*.png"))

            # The reaper runs every two seconds; TTL is two seconds.
            for _ in range(20):
                await asyncio.sleep(0.5)
                current = store.get(job["id"])
                if current and current["status"] == "expired":
                    break
            current = store.get(job["id"])
            assert current is not None
            assert current["status"] == "expired"
            assert current["filename"] is None
            assert not path.exists()
        finally:
            await engine.stop()
            store.close()


async def test_cancel_queued_job(tmp_path):
    (tmp_path / "comfy-output").mkdir()
    with FakeComfyServer(tmp_path / "comfy-output", duration=0.8) as fake:
        settings = make_settings(tmp_path, fake.url)
        store = Store(settings.db_path)
        engine = Engine(settings, store)
        # Do not start the worker: the job stays queued, so cancel is deterministic.
        first = await engine.submit(
            prompt="a cat", negative="", width=512, height=512,
            steps=4, cfg=1.0, seed=1,
        )
        cancelled = await engine.cancel(first["id"])
        assert cancelled is not None
        assert cancelled["status"] == "cancelled"
        assert store.claim_next() is None
        await engine.comfy.close()
        store.close()


async def test_engine_reports_offline_when_comfy_is_down(tmp_path):
    settings = dataclasses.replace(
        Settings(),
        comfy_url="http://127.0.0.1:9",
        comfy_output_dir=tmp_path,
        data_dir=tmp_path / "data",
    )
    store = Store(settings.db_path)
    engine = Engine(settings, store)
    assert engine.ready is False
    await engine.comfy.close()
    store.close()


async def test_lost_prompt_fails_fast_and_frees_the_worker(tmp_path):
    """A prompt that ComfyUI accepted but never ran (interrupted at submit
    time, as a gateway restart does) must fail within the vanish window —
    not spin the single worker until the full job timeout — and the worker
    must pick up the next queued job."""
    (tmp_path / "comfy-output").mkdir()
    with FakeComfyServer(tmp_path / "comfy-output", duration=0.3) as fake:
        settings = make_settings(tmp_path, fake.url)
        store = Store(settings.db_path)
        engine = Engine(settings, store, prompt_vanish_seconds=2)
        fake.comfy.drop_next = 1  # next accepted prompt is silently lost
        await engine.start()
        try:
            first = await engine.submit(
                prompt="lost", negative="", width=512, height=512,
                steps=4, cfg=1.0, seed=1,
            )
            second = await engine.submit(
                prompt="alive", negative="", width=512, height=512,
                steps=4, cfg=1.0, seed=2,
            )
            started = time.time()
            dead = await engine.wait(first["id"], timeout=20)
            elapsed = time.time() - started
            assert dead is not None
            assert dead["status"] == "failed", dead
            assert dead["error"] and "lost the workflow" in dead["error"]
            # Failed inside the vanish window, nowhere near the 20 s timeout.
            assert elapsed < 10, elapsed
            # The worker is free: the queued job behind it completed.
            alive = await engine.wait(second["id"], timeout=30)
            assert alive is not None
            assert alive["status"] == "succeeded", alive.get("error")
            await engine.comfy.close()
        finally:
            await engine.stop()
            store.close()


@pytest.mark.parametrize("cfg,expected", [(1.0, "ConditioningZeroOut"), (2.0, "CLIPTextEncode")])
def test_graph_choice_for_cfg(cfg, expected):
    from gateway.config import Settings as S
    from gateway.workflow import build_workflow

    graph = build_workflow(
        S(), prompt="x", negative="y", width=512, height=512,
        steps=4, cfg=cfg, seed=1,
    )
    assert graph["5"]["class_type"] == expected
