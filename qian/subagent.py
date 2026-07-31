"""子 Agent 配置（Step 13）：fork-return。

类型：
  explore — 只读搜索
  plan    — 只读出方案
  general — 全工具（不含再嵌套 agent）
  自定义  — .qian/agents/*.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tools import DEFINITIONS

READ_ONLY = {"read_file", "list_files", "memory_list", "memory_get", "grep_search"}

EXPLORE_PROMPT = """\
你是 QianAgent 的 explore 子代理：只读搜索代码库。
禁止写文件、改文件、跑会改状态的 shell。
用 list_files / read_file 高效定位，汇报简洁。
"""

PLAN_SUB_PROMPT = """\
你是 QianAgent 的 plan 子代理：只读分析并给出实现计划。
禁止修改任何文件。返回结构化步骤、关键文件与风险。
"""

GENERAL_PROMPT = """\
你是 QianAgent 的 general 子代理：在隔离上下文中独立完成任务。
做完后用简洁报告说明改了什么、结果如何。不要无谓扩 scope。
"""


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, parts[2].lstrip("\n")


def _discover_custom_agents() -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    for base in (Path.home() / ".qian" / "agents", Path.cwd() / ".qian" / "agents"):
        if not base.is_dir():
            continue
        for f in base.glob("*.md"):
            try:
                meta, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
                name = meta.get("name") or f.stem
                allowed = None
                if "allowed-tools" in meta:
                    allowed = [s.strip() for s in meta["allowed-tools"].split(",") if s.strip()]
                agents[name] = {
                    "name": name,
                    "description": meta.get("description", ""),
                    "allowed_tools": allowed,
                    "system_prompt": body,
                }
            except Exception:
                pass
    return agents


def get_sub_agent_config(agent_type: str) -> dict[str, Any]:
    """返回 {system_prompt, tools, type}。tools 为 Anthropic 形状定义列表。"""
    custom = _discover_custom_agents().get(agent_type)
    if custom:
        if custom["allowed_tools"]:
            tools = [t for t in DEFINITIONS if t["name"] in custom["allowed_tools"]]
        else:
            tools = [t for t in DEFINITIONS if t["name"] != "agent"]
        return {
            "system_prompt": custom["system_prompt"],
            "tools": tools,
            "type": agent_type,
        }

    read_tools = [
        t
        for t in DEFINITIONS
        if t["name"] in {"read_file", "list_files", "memory_list", "memory_get"}
    ]
    if agent_type == "explore":
        return {"system_prompt": EXPLORE_PROMPT, "tools": read_tools, "type": "explore"}
    if agent_type == "plan":
        return {"system_prompt": PLAN_SUB_PROMPT, "tools": read_tools, "type": "plan"}
    # general
    tools = [t for t in DEFINITIONS if t["name"] != "agent"]
    return {"system_prompt": GENERAL_PROMPT, "tools": tools, "type": "general"}


def build_agent_type_section() -> str:
    lines = [
        "# Sub-agents (tool: agent)",
        "- explore: 只读快速搜代码",
        "- plan: 只读出实现计划",
        "- general: 全工具独立干活（隔离上下文）",
    ]
    for name, defn in _discover_custom_agents().items():
        lines.append(f"- {name}: {defn.get('description') or 'custom'}")
    return "\n".join(lines) + "\n"
