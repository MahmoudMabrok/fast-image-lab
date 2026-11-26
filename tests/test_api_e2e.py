"""Full-stack smoke test: real gateway process + fake ComfyUI + MCP over HTTP."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from .fake_comfy import FakeComfyServer


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


@pytest.fixture()
def stack(tmp_path, request):
    output = tmp_path / "comfy-output"
    output.mkdir()
    duration = float(getattr(request, "param", 0.6))
    with FakeComfyServer(output, duration=duration) as fake:
        port = free_port()
        env = {
            **os.environ,
            "COMFY_URL": fake.url,
            "COMFY_OUTPUT_DIR": str(output),
            "ANOTHERLAB_DATA_DIR": str(tmp_path / "data"),
            "ANOTHERLAB_TTL_SECONDS": "3",
            "ANOTHERLAB_JOB_TIMEOUT": "30",
            "ANOTHERLAB_DEFAULT_WIDTH": "512",
            "ANOTHERLAB_DEFAULT_HEIGHT": "512",
            "ANOTHERLAB_DEFAULT_STEPS": "4",
            "ANOTHERLAB_API_KEY": "",
        }
        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "gateway.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            wait_for(base + "/api/health")
            yield base, tmp_path / "data"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_ui_and_config_are_served(stack):
    base, _ = stack
    ui = httpx.get(base + "/")
    assert ui.status_code == 200
    assert "Fast Image Lab" in ui.text
    # Five-hour TTL copy (the 5-minute phrasing is gone).
    assert "Images are deleted 5 hours after they are ready." in ui.text
    assert "five minutes" not in ui.text
    # The auto-download preference toggle ships in the served page
    # (opt-in by default; the choice persists in the browser).
    assert 'id="auto-download"' in ui.text
    assert "Auto-download finished images" in ui.text
    # The in-image text composer ships in the no-build UI.
    assert 'id="text-rows"' in ui.text
    assert 'id="add-text"' in ui.text
    assert "Text in image" in ui.text
    config = httpx.get(base + "/api/config").json()
    assert config["ttl_seconds"] == 3
    assert config["mcp"]["http"].endswith("/mcp")


def test_sse_snapshot_is_emitted(stack):
    base, _ = stack
    with httpx.stream("GET", base + "/api/events", timeout=10) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                assert event["type"] == "snapshot"
                assert "status" in event
                return
    raise AssertionError("no SSE snapshot")


def test_generate_image_and_expiry(stack):
    base, data_dir = stack
    job = httpx.post(
        base + "/api/generate",
        json={"prompt": "a cat", "width": 512, "height": 512, "steps": 4, "cfg": 1},
        timeout=10,
    ).json()
    assert job["status"] == "queued"
    assert job["eta_seconds"] >= 0

    for _ in range(60):
        current = httpx.get(base + f"/api/generations/{job['id']}").json()
        if current["status"] in ("succeeded", "failed", "expired"):
            break
        time.sleep(0.25)
    assert current["status"] == "succeeded", current
    assert current["image_url"] == f"/api/images/{job['id']}"

    image = httpx.get(base + current["image_url"])
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG")
    path = data_dir / "images" / f"{job['id']}.png"
    assert path.exists()

    # TTL is three seconds.
    for _ in range(40):
        time.sleep(0.25)
        if httpx.get(base + current["image_url"]).status_code == 410:
            break
    assert httpx.get(base + current["image_url"]).status_code == 410
    assert not path.exists()
    assert httpx.get(base + f"/api/generations/{job['id']}").json()["status"] == "expired"


def test_mcp_over_http_returns_the_png(stack):
    base, _ = stack
    headers = {"Accept": "application/json, text/event-stream"}
    init = httpx.post(
        base + "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    assert init.status_code == 200
    session = init.headers["mcp-session-id"]
    assert init.json()["result"]["serverInfo"]["name"] == "fast-image-lab"
    headers["Mcp-Session-Id"] = session

    tools = httpx.post(
        base + "/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()
    assert {t["name"] for t in tools["result"]["tools"]} >= {"generate_image", "get_generation"}

    call = httpx.post(
        base + "/mcp", headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "generate_image",
                "arguments": {"prompt": "a cat", "wait_seconds": 15},
            },
        },
        timeout=30,
    ).json()
    result = call["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "succeeded"
    images = [c for c in result["content"] if c["type"] == "image"]
    assert images, result
    assert base64.b64decode(images[0]["data"]).startswith(b"\x89PNG")
    assert "deleted at" in result["content"][-1]["text"]

    job_id = result["structuredContent"]["id"]
    poll = httpx.post(
        base + "/mcp", headers=headers,
        json={
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "get_generation", "arguments": {"id": job_id}},
        },
    ).json()
    assert poll["result"]["structuredContent"]["status"] == "succeeded"


@pytest.mark.parametrize("stack", [4.0], indirect=True)
def test_mcp_default_generate_waits_through_a_slow_512(stack):
    """Defaults are 512x512, 4 steps; the call must hold until the engine has
    the job and then wait for the PNG — never hand back a "queued" status."""
    base, _ = stack
    headers = {"Accept": "application/json, text/event-stream"}
    init = httpx.post(
        base + "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]

    started = time.time()
    call = httpx.post(
        base + "/mcp", headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "generate_image",
                "arguments": {"prompt": "a lighthouse", "wait_seconds": 30},
            },
        },
        timeout=60,
    ).json()
    elapsed = time.time() - started
    result = call["result"]
    assert result["isError"] is False, result
    summary = result["structuredContent"]
    assert summary["status"] == "succeeded", summary
    # Server defaults are 512x512, 4 steps (the model's native pace).
    assert (summary["width"], summary["height"], summary["steps"]) == (512, 512, 4)
    # The call held for the whole generation (~4 s fake duration), it did not
    # return early with a queued/running status.
    assert elapsed >= 3.0, elapsed
    for block in result["content"]:
        if block["type"] == "text":
            assert "queued" not in block["text"]
    images = [c for c in result["content"] if c["type"] == "image"]
    assert images, result
    assert base64.b64decode(images[0]["data"]).startswith(b"\x89PNG")


def test_rest_listing_endpoint_is_kept_for_the_web_ui(stack):
    base, _ = stack
    httpx.post(
        base + "/api/generate",
        json={"prompt": "a cat", "width": 512, "height": 512, "steps": 4},
        timeout=10,
    )
    data = httpx.get(base + "/api/generations?limit=24", timeout=10).json()
    assert data["generations"], data
    assert data["generations"][0]["prompt"] == "a cat"


def test_generate_persists_style_look_and_quality_tier(stack):
    base, _ = stack
    job = httpx.post(
        base + "/api/generate",
        json={
            "prompt": "a lighthouse",
            "width": 512,
            "height": 512,
            "steps": 4,
            "cfg": 1,
            "styles": "cinematic,photo",
            "quality": "detail",
        },
        timeout=10,
    ).json()
    # The look and the tier travel with the job, not just as prompt text.
    assert job["styles"] == "cinematic,photo"
    assert job["quality"] == "detail"
    current = httpx.get(base + f"/api/generations/{job['id']}", timeout=10).json()
    assert current["styles"] == "cinematic,photo"
    assert current["quality"] == "detail"
    listed = httpx.get(base + "/api/generations?limit=24", timeout=10).json()
    assert listed["generations"][0]["styles"] == "cinematic,photo"
    assert listed["generations"][0]["quality"] == "detail"


def test_generate_persists_caption_texts(stack):
    """Arabic captions are passed as structured `texts`, not baked into the
    prompt, and come back parsed on every read surface."""
    base, _ = stack
    caption = [{"text": "صباح الخير", "position": "bottom-center", "font": "kufi"}]
    job = httpx.post(
        base + "/api/generate",
        json={"prompt": "a desert", "width": 512, "height": 512, "steps": 4, "texts": caption},
        timeout=10,
    ).json()
    expected = [{
        "text": "صباح الخير", "position": "bottom-center", "color": None, "font": "kufi"
    }]
    assert job["texts"] == expected
    current = httpx.get(base + f"/api/generations/{job['id']}", timeout=10).json()
    assert current["texts"] == expected
    listed = httpx.get(base + "/api/generations?limit=24", timeout=10).json()
    assert listed["generations"][0]["texts"] == expected
    # The caption is not smuggled into the prompt (the model cannot draw it).
    assert "صباح الخير" not in current["prompt"]


def test_generate_rejects_bad_caption(stack):
    base, _ = stack
    resp = httpx.post(
        base + "/api/generate",
        json={
            "prompt": "a cat", "width": 512, "height": 512, "steps": 4,
            "texts": [{"text": "x", "position": "nowhere"}],
        },
        timeout=10,
    )
    assert resp.status_code == 422
    assert "position" in resp.json()["detail"]
