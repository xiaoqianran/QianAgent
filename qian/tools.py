"""工具定义与执行（累计版 = Step 02 + 07 mtime）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "读取文件内容，返回带行号的文本。改文件前必须先 read。",
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
        "description": (
            "写入文件；不存在则创建，存在则整文件覆盖。"
            "覆盖已存在文件前必须先 read_file。"
        ),
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
            "old_string 必须精确匹配且只出现一次。编辑前必须先 read_file。"
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


def execute(
    name: str,
    inp: dict[str, Any],
    read_file_state: dict[str, float] | None = None,
) -> str:
    """执行工具。

    read_file_state: abs_path → mtime（Step 07）。由 Agent 持有并传入。
    """
    try:
        if name == "read_file":
            return _read_file(inp, read_file_state)
        if name in ("write_file", "edit_file"):
            guard = _check_read_before_write(name, inp, read_file_state)
            if guard:
                return guard
        handlers = {
            "write_file": _write_file,
            "edit_file": _edit_file,
            "run_shell": _run_shell,
            "list_files": _list_files,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"Error: 未知工具 {name}"
        result = handler(inp)
        if name in ("write_file", "edit_file") and read_file_state is not None:
            if not str(result).startswith("Error"):
                _touch_state(inp.get("file_path", ""), read_file_state)
        return result
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


def _abs(path_str: str) -> str:
    return str(Path(path_str).expanduser().resolve())


def _touch_state(path_str: str, state: dict[str, float]) -> None:
    if not path_str:
        return
    try:
        abs_path = _abs(path_str)
        state[abs_path] = os.path.getmtime(abs_path)
    except OSError:
        pass


def _check_read_before_write(
    name: str,
    inp: dict[str, Any],
    read_file_state: dict[str, float] | None,
) -> str | None:
    """返回错误字符串；None 表示放行。"""
    if read_file_state is None:
        return None
    path_str = str(inp.get("file_path") or "")
    if not path_str:
        return "Error: missing file_path"
    abs_path = _abs(path_str)
    exists = os.path.exists(abs_path)

    # 新建文件：write 允许；edit 不存在则后面 handler 会报错
    if not exists:
        return None

    verb = "writing" if name == "write_file" else "editing"
    if abs_path not in read_file_state:
        return (
            f"Error: You must read this file before {verb}. "
            f"Use read_file first to see its current contents. "
            f"请先 read_file: {path_str}"
        )
    try:
        current = os.path.getmtime(abs_path)
    except OSError as exc:
        return f"Error: cannot stat file: {exc}"
    if current != read_file_state[abs_path]:
        return (
            f"Error: {path_str} was modified externally since your last read "
            f"(mtime changed). Please read_file again before {verb}. "
            f"文件已被外部修改，请重读后再{('写入' if name == 'write_file' else '编辑')}。"
        )
    return None


def _read_file(inp: dict[str, Any], read_file_state: dict[str, float] | None) -> str:
    path = Path(inp["file_path"])
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))
    if read_file_state is not None:
        _touch_state(str(path), read_file_state)
    # Step 08 会做大结果落盘；这里只做极粗安全上限
    if len(numbered) > 200_000:
        return numbered[:200_000] + "\n\n[... hard truncated in tools layer ...]"
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
        if any(
            part.startswith(".") or part in {"node_modules", "__pycache__", ".venv"}
            for part in parts
        ):
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
