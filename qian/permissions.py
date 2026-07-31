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
    "read_file",
    "list_files",
    "memory_list",
    "memory_get",
    "skill",
}
EDIT_TOOLS = {"write_file", "edit_file"}
PLAN_CONTROL_TOOLS = {"enter_plan_mode", "exit_plan_mode"}
MEMORY_WRITE_TOOLS = {"memory_save"}

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


def check_permission(
    tool_name: str,
    inp: dict[str, Any],
    mode: PermissionMode = "default",
    *,
    plan_file_path: str | None = None,
) -> dict[str, str]:
    """返回 {"action": "allow"|"deny"|"confirm", "message": "..."}。"""
    mode = mode or "default"

    # Plan：只读 + 可写计划文件 + 规划控制
    if mode == "plan":
        if tool_name in READ_TOOLS or tool_name in PLAN_CONTROL_TOOLS:
            return {"action": "allow", "message": ""}
        if tool_name in EDIT_TOOLS and plan_file_path:
            target = str(Path(str(inp.get("file_path") or "")).expanduser().resolve())
            plan_abs = str(Path(plan_file_path).expanduser().resolve())
            if target == plan_abs:
                return {"action": "allow", "message": ""}
        return {
            "action": "deny",
            "message": f"plan 模式禁止 {tool_name}（只读规划；仅可写计划文件）",
        }

    # Yolo
    if mode == "bypass":
        return {"action": "allow", "message": ""}

    # 读工具 / 规划控制 / 记忆写入默认放行
    if tool_name in READ_TOOLS or tool_name in PLAN_CONTROL_TOOLS:
        return {"action": "allow", "message": ""}
    if tool_name in MEMORY_WRITE_TOOLS:
        return {"action": "allow", "message": ""}

    needs_confirm = False
    confirm_message = ""

    if tool_name == "run_shell":
        cmd = str(inp.get("command") or "")
        if is_dangerous_command(cmd):
            needs_confirm = True
            confirm_message = f"危险命令: {cmd}"
    elif tool_name == "write_file":
        path = Path(str(inp.get("file_path") or ""))
        if not path.exists():
            needs_confirm = True
            confirm_message = f"创建新文件: {path}"
    elif tool_name == "edit_file":
        path = Path(str(inp.get("file_path") or ""))
        if not path.exists():
            needs_confirm = True
            confirm_message = f"编辑不存在的文件: {path}"

    if needs_confirm:
        if mode == "dontAsk":
            return {
                "action": "deny",
                "message": f"dontAsk 模式自动拒绝: {confirm_message}",
            }
        return {"action": "confirm", "message": confirm_message}

    return {"action": "allow", "message": ""}
