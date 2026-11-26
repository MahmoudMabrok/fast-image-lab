"""MCP surface: handshake, tool discovery, validation and waiting."""

from __future__ import annotations

import dataclasses

import pytest

from gateway.config import Settings
from gateway.engine import Engine
from gateway.mcp_http import McpServer, build_tools
from gateway.store import Store

from .fake_comfy import FakeComfyServer


def make_settings(tmp_path) -> Settings:
    return dataclasses.replace(
        Settings(),
        data_dir=tmp_path,
        comfy_url="http://127.0.0.1:9",
        public_url="https://example.test",
        api_key="",
    )


@pytest.fixture()
def server(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    engine = Engine(settings, store)
    yield McpServer(settings, engine)
    store.close()


@pytest.fixture()
async def live_server(tmp_path):
    """MCP server against a started engine and a fake ComfyUI that accepts a
    prompt and then samples forever, so jobs sit in "running"."""
    output = tmp_path / "comfy-output"
    output.mkdir()
    with FakeComfyServer(output, duration=3600, steps=1) as fake:
        settings = dataclasses.replace(
            Settings(),
            data_dir=tmp_path / "data",
            comfy_url=fake.url,
            comfy_output_dir=output,
            public_url="https://example.test",
            api_key="",
        )
        store = Store(settings.db_path)
        engine = Engine(settings, store)
        await engine.start()
        yield McpServer(settings, engine)
        await engine.stop()
        store.close()


async def test_initialize_returns_a_session(server):
    result, headers = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        None,
    )
    assert result is not None
    assert result["result"]["protocolVersion"] == "2025-06-18"
    assert result["result"]["serverInfo"]["name"] == "fast-image-lab"
    assert headers["Mcp-Session-Id"]


async def test_tools_list_exposes_the_generation_surface(server):
    result, _ = await server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, None
    )
    assert result is not None
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert names == {
        "generate_image",
        "get_generation",
        "cancel_generation",
        "engine_info",
    }
    # No listing tool: other clients must not be able to read other
    # generations, only their own (by id).
    assert "list_generations" not in names
    # Distilled model: no LoRA parameter anywhere in the schema.
    schema = next(t for t in result["result"]["tools"] if t["name"] == "generate_image")
    assert "lora_strength" not in schema["inputSchema"]["properties"]


async def test_resources_list_has_no_generation_listing(server):
    result, _ = await server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}}, None
    )
    assert result is not None
    uris = {resource["uri"] for resource in result["result"]["resources"]}
    assert uris == {"fast-image-lab://engine"}

    read, _ = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "fast-image-lab://generations"},
        },
        None,
    )
    assert read is not None
    assert read["error"]["code"] == -32602


async def test_generate_requires_a_prompt(server):
    result, _ = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "generate_image", "arguments": {"prompt": "  "}},
        },
        None,
    )
    assert result is not None
    assert result["result"]["isError"] is True
    assert "prompt" in result["result"]["content"][0]["text"]


async def test_generate_returns_no_earlier_than_a_running_engine(live_server):
    """wait_seconds=0 must not hand back a job the engine does not hold: the
    engine is single-worker and takes a job within ~0.4 s, so the answer has
    to be "running" (or later), never "queued"."""
    import time as _time

    t0 = _time.time()
    result, _ = await live_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "generate_image",
                "arguments": {"prompt": "a cat", "wait_seconds": 0},
            },
        },
        None,
    )
    assert result is not None
    payload = result["result"]
    assert payload["isError"] is False
    summary = payload["structuredContent"]
    assert summary["status"] == "running", summary
    # No one is ahead; the count includes the running job itself.
    assert summary["queue_position"] == 1
    assert "id=img_" in payload["content"][-1]["text"]
    assert summary["id"].startswith("img_")
    assert live_server.engine.store.get(summary["id"]) is not None
    # It waited for the engine to take the job instead of returning instantly.
    assert _time.time() - t0 >= 0.2
    # Clean up so the fixture's engine.stop() has nothing to cancel.
    await live_server.engine.cancel(summary["id"])


async def test_unknown_method_is_a_jsonrpc_error(server):
    result, _ = await server.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "nope", "params": {}}, None
    )
    assert result is not None
    assert result["error"]["code"] == -32601


def test_tool_schemas_follow_the_dimension_limits():
    tools = {tool["name"]: tool for tool in build_tools(Settings())}
    schema = tools["generate_image"]["inputSchema"]
    assert schema["properties"]["width"]["multipleOf"] == 16
    assert schema["properties"]["height"]["minimum"] == Settings().min_dimension
    # The distilled model's native pace is 4 steps.
    assert schema["properties"]["steps"]["default"] == 4
    assert schema["properties"]["steps"]["minimum"] == 4
    assert schema["required"] == ["prompt"]


def test_generate_schema_exposes_multilingual_texts():
    schema = {t["name"]: t for t in build_tools(Settings())}["generate_image"]["inputSchema"]
    texts = schema["properties"]["texts"]
    assert texts["type"] == "array"
    assert texts["maxItems"] == 4
    item = texts["items"]
    assert item["required"] == ["text"]
    assert item["properties"]["font"]["enum"] == ["naskh", "kufi", "sans"]
    assert len(item["properties"]["position"]["enum"]) == 7
    # The distilled model has no LoRA; captions are drawn, not baked in.
    assert "lora_strength" not in schema["properties"]


async def test_generate_passes_texts(live_server):
    result, _ = await live_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "generate_image",
                "arguments": {
                    "prompt": "a cat",
                    "wait_seconds": 0,
                    "texts": [
                        {"text": "قطة", "position": "top-left", "font": "kufi"},
                        {"text": "القط", "position": "bottom-right"},
                    ],
                },
            },
        },
        None,
    )
    assert result is not None
    assert result["result"]["isError"] is False, result
    summary = result["result"]["structuredContent"]
    assert summary["texts"] == [
        {"text": "قطة", "position": "top-left", "color": None, "font": "kufi"},
        {"text": "القط", "position": "bottom-right", "color": None, "font": "naskh"},
    ]
    await live_server.engine.cancel(summary["id"])


async def test_generate_rejects_bad_texts(live_server):
    result, _ = await live_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "generate_image",
                "arguments": {
                    "prompt": "a cat",
                    "texts": [{"text": "x", "font": "comic-sans"}],
                },
            },
        },
        None,
    )
    assert result is not None
    assert result["result"]["isError"] is True
    assert "font" in result["result"]["content"][0]["text"]
