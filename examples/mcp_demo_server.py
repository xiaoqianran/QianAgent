#!/usr/bin/env python3
"""最小 MCP stdio server（Step 18 demo）。

协议：newline-delimited JSON-RPC。
仅实现 initialize / tools/list / tools/call。
"""

from __future__ import annotations

import json
import sys
from typing import Any


TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers a + b.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
]


def respond(msg_id: Any, result: Any) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def respond_error(msg_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def handle(req: dict[str, Any]) -> None:
    method = req.get("method")
    msg_id = req.get("id")
    params = req.get("params") or {}

    # notifications：无 id，忽略
    if msg_id is None:
        return

    if method == "initialize":
        respond(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "qian-mcp-demo", "version": "0.1.0"},
            },
        )
        return

    if method == "tools/list":
        respond(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            text = str(args.get("text", ""))
            respond(
                msg_id,
                {"content": [{"type": "text", "text": text}]},
            )
            return
        if name == "add":
            try:
                a = float(args.get("a", 0))
                b = float(args.get("b", 0))
                total = a + b
                text = str(int(total)) if total == int(total) else str(total)
                respond(
                    msg_id,
                    {"content": [{"type": "text", "text": text}]},
                )
            except Exception as exc:
                respond_error(msg_id, -32602, str(exc))
            return
        respond_error(msg_id, -32601, f"unknown tool: {name}")
        return

    respond_error(msg_id, -32601, f"unknown method: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(req)


if __name__ == "__main__":
    main()
