"""工具定义与执行（累计版 = Step 02）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "读取文件内容，返回带行号的文本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入文件；不存在则创建，存在则整文件覆盖。",
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
        "description": (
            "把文件中唯一出现的 old_string 替换为 new_string。"
            "old_string 必须精确匹配且在文件中只出现一次。"
        ),
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
        "description": "执行 shell 命令，返回 stdout/stderr。用于测试、安装、git 等。",
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
    {
        "name": "list_files",
        "description": "按 glob 列出文件路径。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": '例如 "**/*.py"',
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录，默认当前目录",
                },
            },
            "required": ["pattern"],
        },
    },
]


def to_openai_tools(defs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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


def execute(name: str, inp: dict[str, Any]) -> str:
    handlers = {
        "read_file": _read_file,
        "write_file": _write_file,
        "edit_file": _edit_file,
        "run_shell": _run_shell,
        "list_files": _list_files,
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
    numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))
    # 过长结果先粗暴截断（Step 08 会做落盘）
    if len(numbered) > 80000:
        return numbered[:80000] + "\n\n[... truncated ...]"
    return numbered


def _write_file(inp: dict[str, Any]) -> str:
    path = Path(inp["file_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = inp["content"]
    path.write_text(content, encoding="utf-8")
    return f"Wrote {path} ({len(content.splitlines())} lines)"


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
        return f"exit={result.returncode}\nstdout:\n{out}\nstderr:\n{err}"
    return out or "(no output)"


def _list_files(inp: dict[str, Any]) -> str:
    base = Path(inp.get("path") or ".")
    pattern = inp["pattern"]
    files: list[str] = []
    extra = 0
    for p in base.glob(pattern):
        if not p.is_file():
            continue
        parts = p.parts
        if any(part.startswith(".") or part in {"node_modules", "__pycache__", ".venv"} for part in parts):
            continue
        if len(files) < 200:
            try:
                files.append(str(p.relative_to(base)) if base != Path(".") else str(p))
            except ValueError:
                files.append(str(p))
        else:
            extra += 1
    if not files:
        return "No files matched."
    text = "\n".join(files)
    if extra:
        text += f"\n... and {extra} more"
    return text


def format_call(name: str, inp: dict[str, Any]) -> str:
    return f"{name}({json.dumps(inp, ensure_ascii=False)})"
