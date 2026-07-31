"""MCP 客户端（Step 14）：JSON-RPC over stdio，同步实现。

配置（后者覆盖前者）:
  ~/.qian/settings.json
  ./.qian/settings.json
  ~/.claude/settings.json
  ./.claude/settings.json
  ./.mcp.json

形状:
  { "mcpServers": { "name": { "command": "...", "args": [...], "env": {} } } }

工具名: mcp__<server>__<tool>
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any


class McpConnection:
    def __init__(
        self,
        server_name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._lock = threading.Lock()

    def connect(self) -> None:
        env = {**os.environ, **self.env}
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )

    def _request(self, method: str, params: dict | None = None) -> Any:
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError(f"MCP '{self.server_name}' not connected")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
            # 读到匹配 id 的响应（跳过 notification）
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"MCP '{self.server_name}' closed unexpectedly")
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("id") != req_id:
                    continue
                if "error" in data:
                    err = data["error"]
                    raise RuntimeError(
                        f"MCP error {err.get('code')}: {err.get('message')}"
                    )
                return data.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        if not self._proc or not self._proc.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self._lock:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()

    def initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qian-agent", "version": "0.5.0"},
            },
        )
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        if not result or not isinstance(result.get("tools"), list):
            return []
        out = []
        for t in result["tools"]:
            out.append(
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema")
                    or {"type": "object", "properties": {}},
                    "serverName": self.server_name,
                }
            )
        return out

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": args})
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            return "\n".join(
                c.get("text", "")
                for c in result["content"]
                if c.get("type") == "text"
            )
        return json.dumps(result, ensure_ascii=False)

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None


class McpManager:
    def __init__(self) -> None:
        self._connections: dict[str, McpConnection] = {}
        self._tools: list[dict[str, Any]] = []
        self._connected = False

    def load_and_connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        configs = self._load_configs()
        for name, cfg in configs.items():
            conn = McpConnection(
                name,
                cfg["command"],
                cfg.get("args") or [],
                cfg.get("env") or {},
            )
            try:
                conn.connect()
                conn.initialize()
                tools = conn.list_tools()
                self._connections[name] = conn
                self._tools.extend(tools)
                print(f"[mcp] connected '{name}' — {len(tools)} tool(s)", flush=True)
            except Exception as exc:
                print(f"[mcp] failed '{name}': {exc}", flush=True)
                conn.close()

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": f"mcp__{t['serverName']}__{t['name']}",
                "description": t.get("description")
                or f"MCP tool {t['name']} from {t['serverName']}",
                "input_schema": t.get("inputSchema")
                or {"type": "object", "properties": {}},
            }
            for t in self._tools
        ]

    def is_mcp_tool(self, name: str) -> bool:
        return name.startswith("mcp__")

    def call_tool(self, prefixed: str, args: dict[str, Any]) -> str:
        parts = prefixed.split("__")
        if len(parts) < 3:
            return f"Error: invalid MCP tool name {prefixed}"
        server = parts[1]
        tool = "__".join(parts[2:])
        conn = self._connections.get(server)
        if not conn:
            return f"Error: MCP server '{server}' not connected"
        try:
            return conn.call_tool(tool, args)
        except Exception as exc:
            return f"Error: MCP call failed: {exc}"

    def disconnect_all(self) -> None:
        for c in self._connections.values():
            c.close()
        self._connections.clear()
        self._tools.clear()
        self._connected = False

    def _load_configs(self) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        paths = [
            Path.home() / ".qian" / "settings.json",
            Path.cwd() / ".qian" / "settings.json",
            Path.home() / ".claude" / "settings.json",
            Path.cwd() / ".claude" / "settings.json",
            Path.cwd() / ".mcp.json",
        ]
        for p in paths:
            self._merge(p, merged)
        return merged

    def _merge(self, path: Path, target: dict[str, dict[str, Any]]) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            servers = raw.get("mcpServers", raw if isinstance(raw, dict) else {})
            if not isinstance(servers, dict):
                return
            for name, cfg in servers.items():
                if isinstance(cfg, dict) and "command" in cfg:
                    target[name] = cfg
        except Exception:
            pass
