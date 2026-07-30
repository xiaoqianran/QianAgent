"""Step 02: 四个最小工具 —— 定义 schema + 本地执行。

工具系统只有两半：
1. DEFINITIONS  → 告诉模型「有什么工具、参数长什么样」
2. execute()    → 模型要调用时，我们真的去跑
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# ─── 1) Schema（Anthropic 形状；OpenAI 侧会再转一层）────────

DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "读取文件内容，返回带行号的文本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入文件；不存在则创建，存在则覆盖。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "把文件中唯一出现的 old_string 替换为 new_string。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "run_shell",
        "description": "执行 shell 命令并返回 stdout/stderr。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {
                    "type": "number",
                    "description": "超时毫秒，默认 30000",
                },
            },
            "required": ["command"],
        },
    },
]


def to_openai_tools(defs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Anthropic schema → OpenAI tools 格式。"""
    defs = defs or DEFINITIONS
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["input_schema"],
            },
        }
        for d in defs
    ]


# ─── 2) 执行器 ─────────────────────────────────────────────


def execute(name: str, inp: dict[str, Any]) -> str:
    handlers = {
        "read_file": _read_file,
        "write_file": _write_file,
        "edit_file": _edit_file,
        "run_shell": _run_shell,
    }
    handler = handlers.get(name)
    if handler is None:
        return f"Error: 未知工具 {name}"
    try:
        return handler(inp)
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


def _read_file(inp: dict[str, Any]) -> str:
    path = Path(inp["file_path"])
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))


def _write_file(inp: dict[str, Any]) -> str:
    path = Path(inp["file_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = inp["content"]
    path.write_text(content, encoding="utf-8")
    n = len(content.splitlines())
    return f"Wrote {path} ({n} lines)"


def _edit_file(inp: dict[str, Any]) -> str:
    path = Path(inp["file_path"])
    content = path.read_text(encoding="utf-8")
    old = inp["old_string"]
    new = inp["new_string"]
    count = content.count(old)
    if count == 0:
        return f"Error: old_string 未在 {path} 中找到"
    if count > 1:
        return f"Error: old_string 出现 {count} 次，必须唯一"
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return f"Edited {path}"


def _run_shell(inp: dict[str, Any]) -> str:
    timeout_ms = float(inp.get("timeout") or 30000)
    result = subprocess.run(
        inp["command"],
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_ms / 1000,
    )
    out = result.stdout or ""
    err = result.stderr or ""
    if result.returncode != 0:
        return (
            f"exit={result.returncode}\n"
            f"stdout:\n{out}\n"
            f"stderr:\n{err}"
        )
    return out or "(no output)"


def dump_call(name: str, inp: dict[str, Any]) -> str:
    return f"{name}({json.dumps(inp, ensure_ascii=False)})"
