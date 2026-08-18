"""工具定义与执行（累计版 = Step 02 + 07 mtime）。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .background import TOOL_DEFINITIONS as BACKGROUND_TOOL_DEFINITIONS
from .goals import TOOL_DEFINITIONS as GOAL_TOOL_DEFINITIONS
from .scheduler import TOOL_DEFINITIONS as CRON_TOOL_DEFINITIONS
from .tasks import TOOL_DEFINITIONS as TASK_TOOL_DEFINITIONS
from .teams import TOOL_DEFINITIONS as TEAM_TOOL_DEFINITIONS
from .todo import TOOL_DEFINITION as TODO_TOOL_DEFINITION
from .workflows import TOOL_DEFINITIONS as WORKFLOW_TOOL_DEFINITIONS
from .worktrees import TOOL_DEFINITIONS as WORKTREE_TOOL_DEFINITIONS

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
    # ── Step 09 Context compact ───────────────────────────
    {
        "name": "compact",
        "description": (
            "将长对话历史压缩为可继续执行的摘要。通常自动触发；"
            "当上下文已经很长且后续任务仍复杂时可主动调用。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Step 10 记忆 ───────────────────────────────────────
    {
        "name": "memory_save",
        "description": (
            "保存一条跨会话项目记忆到 ~/.qian/projects/.../memory/。"
            "type: user|project|feedback|reference。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "type": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "memory_list",
        "description": "列出当前项目已保存的记忆。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_get",
        "description": "读取一条记忆全文。参数 filename 或 name。",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "name": {"type": "string"},
            },
        },
    },
    # ── Step 11 Skills ─────────────────────────────────────
    {
        "name": "skill",
        "description": "调用已注册 skill（.qian/skills/*/SKILL.md）。返回 skill 指令文本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "args": {"type": "string"},
            },
            "required": ["skill_name"],
        },
    },
    # ── Step 12 Plan mode ──────────────────────────────────
    {
        "name": "enter_plan_mode",
        "description": "进入只读规划模式，只能读文件并写计划文件。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "exit_plan_mode",
        "description": "结束规划：展示计划文件并请求用户审批。",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Step 13 子 Agent ───────────────────────────────────
    {
        "name": "agent",
        "description": (
            "启动隔离子 Agent。type: explore(只读搜索)|plan(只读方案)|general(全工具)。"
            "返回子 Agent 的文本报告。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "3-5 词短描述",
                },
                "prompt": {
                    "type": "string",
                    "description": "给子 Agent 的详细任务",
                },
                "type": {
                    "type": "string",
                    "enum": ["explore", "plan", "general"],
                    "description": "默认 general",
                },
            },
            "required": ["description", "prompt"],
        },
    },
]

# ── Step 19-27 runtime extensions ─────────────────────────
DEFINITIONS.extend(
    [TODO_TOOL_DEFINITION]
    + TASK_TOOL_DEFINITIONS
    + BACKGROUND_TOOL_DEFINITIONS
    + CRON_TOOL_DEFINITIONS
    + TEAM_TOOL_DEFINITIONS
    + WORKFLOW_TOOL_DEFINITIONS
    + GOAL_TOOL_DEFINITIONS
    + WORKTREE_TOOL_DEFINITIONS
)

# Agent 层处理的特殊工具（不在 tools.execute 里跑）
AGENT_SCOPED_TOOLS = {
    "compact", "memory_save", "memory_list", "memory_get", "skill",
    "enter_plan_mode", "exit_plan_mode", "agent",
    "todo_write",
    "task_create", "task_list", "task_get", "task_claim", "task_complete", "task_update",
    "background_run", "background_check", "background_list", "background_cancel",
    "schedule_cron", "list_crons", "cancel_cron",
    "team_spawn", "team_send", "team_broadcast", "team_inbox", "team_list",
    "team_shutdown", "team_plan_review",
    "workflow_list", "workflow_run", "workflow_resume", "workflow_status",
    "goal_set", "goal_status", "goal_clear",
    "worktree_create", "worktree_list", "worktree_status", "worktree_run",
    "worktree_keep", "worktree_remove",
}

# Step 17: 无副作用、可并行的工具
CONCURRENCY_SAFE_TOOLS = {
    "read_file", "list_files", "memory_list", "memory_get", "skill",
    "task_list", "task_get",
    "background_check", "background_list",
    "list_crons", "team_list",
    "workflow_list", "workflow_status",
    "goal_status", "worktree_list", "worktree_status",
}


def is_concurrency_safe(name: str) -> bool:
    if name.startswith("mcp__"):
        # MCP 默认保守：不并行（未知副作用）
        return False
    return name in CONCURRENCY_SAFE_TOOLS


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


def _shell_popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _cleanup_shell_process_group(process: subprocess.Popen[str], *, force: bool = False) -> None:
    """Best-effort cleanup of descendants spawned by a shell command.

    On POSIX the session/process-group ID remains usable even after the shell
    itself exits, which prevents commands such as ``nohup ... &`` from leaking
    helpers beyond the tool call. Windows uses ``taskkill /T`` while the parent
    is still addressable.
    """
    try:
        if os.name == "nt":
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, timeout=3, check=False,
                )
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.killpg(process.pid, sig)
        if not force:
            time.sleep(0.05)
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _run_shell(inp: dict[str, Any]) -> str:
    timeout_ms = float(inp.get("timeout") or 30000)
    process = subprocess.Popen(
        inp["command"],
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_shell_popen_kwargs(),
    )
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout_ms / 1000)
    except subprocess.TimeoutExpired:
        timed_out = True
        _cleanup_shell_process_group(process, force=True)
        out, err = process.communicate()
    finally:
        # Also reap detached/background descendants after a successful shell exit.
        _cleanup_shell_process_group(process)

    out = out or ""
    err = err or ""
    if timed_out:
        return f"Error: Timeout ({timeout_ms / 1000:g}s)\nstdout:\n{out}\nstderr:\n{err}"
    if process.returncode != 0:
        return f"exit={process.returncode}\nstdout:\n{out}\nstderr:\n{err}"
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
