"""Stdio bridge: expose a remote AnotherLab MCP endpoint to a local agent.

    python -m gateway.mcp_stdio --url https://<machine>.kube-labs.net/mcp

Add to an agent's MCP config with command=python and those args.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="AnotherLab MCP stdio bridge")
    parser.add_argument("--url", required=True, help="Streamable HTTP MCP endpoint")
    parser.add_argument("--key", default="", help="API key, if the server requires one")
    args = parser.parse_args()

    headers = {"Accept": "application/json, text/event-stream"}
    if args.key:
        headers["Authorization"] = f"Bearer {args.key}"

    session: str | None = None
    with httpx.Client(timeout=900.0) as client:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_headers = dict(headers)
            if session:
                request_headers["Mcp-Session-Id"] = session
            try:
                response = client.post(args.url, json=payload, headers=request_headers)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32000, "message": str(exc)}}), flush=True)
                continue
            session = response.headers.get("Mcp-Session-Id", session)
            if response.status_code == 202 or not response.content:
                continue
            text = response.text.strip()
            if text.startswith("data:"):
                text = "\n".join(l[5:].strip() for l in text.splitlines() if l.startswith("data:"))
            print(text, flush=True)


if __name__ == "__main__":
    main()
