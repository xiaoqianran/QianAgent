"""权限检查（Step 06）。

check_permission(tool, input, mode) → {action, message}

action:
  - allow
  - deny
  - confirm  （需要用户点头；CLI 层负责问 y/n）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PermissionMode = str  # default | bypass | dontAsk | plan

READ_TOOLS = {
    "read_file", "list_files", "compact", "memory_list", "memory_get", "skill",
    "task_list", "task_get", "background_check", "background_list",
    "list_crons", "team_list", "workflow_list", "workflow_status",
    "goal_status", "worktree_list", "worktree_status",
}
EDIT_TOOLS = {"write_file", "edit_file"}
PLAN_CONTROL_TOOLS = {"enter_plan_mode", "exit_plan_mode"}
MEMORY_WRITE_TOOLS = {"memory_save"}
AGENT_LAUNCH_TOOLS = {"agent"}
INTERNAL_STATE_TOOLS = {
    "todo_write", "task_create", "task_claim", "task_complete", "task_update",
    "goal_set", "goal_clear", "background_cancel", "cancel_cron",
    "team_send", "team_broadcast", "team_inbox", "team_shutdown", "team_plan_review",
    "worktree_keep",
}
AUTONOMY_CONFIRM_TOOLS = {
    "schedule_cron", "team_spawn", "workflow_run", "workflow_resume",
    "worktree_create", "worktree_remove",
}

# 高危命令粗检（够教学用，不是安全边界的全部）
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f|-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bgit\s+push\s+.*--force\b", re.I),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bdd\s+if=", re.I),
    re.compile(r">\s*/dev/sd", re.I),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b", re.I),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b", re.I),
    re.compile(r"\bchmod\s+-R\s+777\b", re.I),
]


def is_dangerous_command(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_PATTERNS)


def _is_outside_workspace(path_value: str) -> bool:
    if not path_value:
        return False
    try:
        target = Path(path_value).expanduser().resolve()
        workspace = Path.cwd().resolve()
        return not target.is_relative_to(workspace)
    except Exception:
        return True


def _command_for_tool(tool_name: str, inp: dict[str, Any]) -> str:
    if tool_name in {"run_shell", "background_run", "worktree_run"}:
        return str(inp.get("command") or "")
    return ""


def check_permission(
    tool_name: str,
    inp: dict[str, Any],
    mode: PermissionMode = "default",
    *,
    plan_file_path: str | None = None,
) -> dict[str, str]:
    """Return ``allow`` / ``deny`` / ``confirm`` for a tool invocation."""
    mode = mode or "default"

    # Plan mode is read-only except its dedicated plan file and mode controls.
    if mode == "plan":
        if tool_name in READ_TOOLS or tool_name in PLAN_CONTROL_TOOLS:
            if tool_name in {"read_file", "list_files"}:
                path_value = str((inp.get("path") if tool_name == "list_files" else inp.get("file_path")) or "")
                if path_value and _is_outside_workspace(path_value):
                    return {"action": "deny", "message": "plan 模式禁止访问工作区外路径"}
            return {"action": "allow", "message": ""}
        if tool_name in EDIT_TOOLS and plan_file_path:
            target = str(Path(str(inp.get("file_path") or "")).expanduser().resolve())
            plan_abs = str(Path(plan_file_path).expanduser().resolve())
            if target == plan_abs:
                return {"action": "allow", "message": ""}
        return {"action": "deny", "message": f"plan 模式禁止 {tool_name}（只读规划；仅可写计划文件）"}

    if mode == "bypass":
        return {"action": "allow", "message": ""}

    # Path access outside the workspace is never silently granted in normal mode.
    if tool_name in {"read_file", "write_file", "edit_file", "list_files"}:
        path_value = str((inp.get("path") if tool_name == "list_files" else inp.get("file_path")) or "")
        if _is_outside_workspace(path_value):
            message = f"访问工作区外路径: {path_value}"
            if mode == "dontAsk":
                return {"action": "deny", "message": f"dontAsk 模式自动拒绝: {message}"}
            return {"action": "confirm", "message": message}

    if tool_name in READ_TOOLS or tool_name in PLAN_CONTROL_TOOLS:
        return {"action": "allow", "message": ""}
    if tool_name in MEMORY_WRITE_TOOLS or tool_name in AGENT_LAUNCH_TOOLS or tool_name in INTERNAL_STATE_TOOLS:
        return {"action": "allow", "message": ""}

    if tool_name in AUTONOMY_CONFIRM_TOOLS:
        detail = (
            inp.get("cron") or inp.get("name") or inp.get("run_id")
            or inp.get("job_id") or tool_name
        )
        message = f"启动高层编排/自治操作 {tool_name}: {detail}"
        if mode == "dontAsk":
            return {"action": "deny", "message": f"dontAsk 模式自动拒绝: {message}"}
        return {"action": "confirm", "message": message}

    cmd = _command_for_tool(tool_name, inp)
    if cmd:
        if is_dangerous_command(cmd):
            message = f"危险命令: {cmd}"
            if mode == "dontAsk":
                return {"action": "deny", "message": f"dontAsk 模式自动拒绝: {message}"}
            return {"action": "confirm", "message": message}
        return {"action": "allow", "message": ""}

    if tool_name == "write_file":
        path = Path(str(inp.get("file_path") or ""))
        if not path.exists():
            message = f"创建新文件: {path}"
            if mode == "dontAsk":
                return {"action": "deny", "message": f"dontAsk 模式自动拒绝: {message}"}
            return {"action": "confirm", "message": message}
    if tool_name == "edit_file":
        path = Path(str(inp.get("file_path") or ""))
        if not path.exists():
            message = f"编辑不存在的文件: {path}"
            if mode == "dontAsk":
                return {"action": "deny", "message": f"dontAsk 模式自动拒绝: {message}"}
            return {"action": "confirm", "message": message}

    return {"action": "allow", "message": ""}
