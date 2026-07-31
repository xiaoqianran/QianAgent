"""QianAgent 核心：messages + tool-loop。

累计步骤：
  01 Agent Loop
  02 Tools
  03 System Prompt
  04 Session
  05 Streaming      ← 文本边生成边打印
  06 Permissions    ← 工具执行前 allow/deny/confirm
  07 mtime          ← 读前再改
  08 大结果落盘      ← context.persist_large_result
  09 snip/compact   ← 旧 tool_result 裁剪 + 摘要
  10 memory         ← 项目级文件记忆
  11 skills         ← SKILL.md
  12 plan mode      ← 只读规划 + 审批
  13 subagent       ← agent 工具 fork-return
  14 MCP            ← mcp__server__tool
  15 budget/abort   ← max_turns / max_cost / Ctrl+C
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .context import (
    estimate_chars,
    maybe_snip_messages,
    persist_large_result,
    summarize_for_compact,
)
from . import memory as memory_mod
from .mcp_client import McpManager
from .permissions import PermissionMode, check_permission
from .prompt import build_system_prompt
from . import skills as skills_mod
from . import subagent as subagent_mod
from .tools import AGENT_SCOPED_TOOLS, DEFINITIONS, execute, format_call, to_openai_tools

# confirm_fn(message) -> bool
ConfirmFn = Callable[[str], bool]
# plan_approval_fn(plan_text) -> {"choice": str, "feedback": str|None}
PlanApprovalFn = Callable[[str], dict[str, Any]]

# 粗估：约 4 字符 ≈ 1 token；超过该字符预算触发 snip
DEFAULT_SNIP_CHAR_BUDGET = 120_000


def detect_backend() -> tuple[str, dict[str, Any]]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        kwargs: dict[str, Any] = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
        if os.environ.get("ANTHROPIC_BASE_URL"):
            kwargs["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
        return "anthropic", kwargs

    if os.environ.get("OPENAI_API_KEY"):
        kwargs = {"api_key": os.environ["OPENAI_API_KEY"]}
        if os.environ.get("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
        return "openai", kwargs

    raise RuntimeError(
        "请设置 ANTHROPIC_API_KEY，或 OPENAI_API_KEY（可选 OPENAI_BASE_URL）"
    )


def default_model(backend: str) -> str:
    return os.environ.get("QIAN_MODEL") or (
        "claude-sonnet-4-6" if backend == "anthropic" else "gpt-4o"
    )


class Agent:
    def __init__(
        self,
        *,
        model: str | None = None,
        max_tool_loops: int = 30,
        verbose_tools: bool = True,
        stream: bool = True,
        permission_mode: PermissionMode = "default",
        confirm_fn: ConfirmFn | None = None,
        max_turns: int | None = None,
        max_cost_usd: float | None = None,
        is_sub_agent: bool = False,
        custom_system_prompt: str | None = None,
        custom_tools: list[dict[str, Any]] | None = None,
        enable_mcp: bool = True,
    ) -> None:
        self.backend, client_kwargs = detect_backend()
        self.model = model or default_model(self.backend)
        self.max_tool_loops = max_tool_loops
        self.verbose_tools = verbose_tools and not is_sub_agent
        self.stream = stream and not is_sub_agent
        self.permission_mode = permission_mode
        self.confirm_fn = confirm_fn
        self.plan_approval_fn: PlanApprovalFn | None = None
        self.max_turns = max_turns
        self.max_cost_usd = max_cost_usd
        self.is_sub_agent = is_sub_agent
        self._aborted = False
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # 同类 confirm 只问一次（用 message 字符串当 key）
        self._confirmed: set[str] = set()
        # Step 07: abs_path → mtime
        self._read_file_state: dict[str, float] = {}
        self.messages: list[dict[str, Any]] = []
        self.turn_count = 0
        self.snip_char_budget = DEFAULT_SNIP_CHAR_BUDGET
        self.compact_count = 0
        # Step 12 plan
        self._pre_plan_mode: str | None = None
        self._plan_file_path: str | None = None
        if permission_mode == "plan" and not is_sub_agent:
            self._plan_file_path = self._new_plan_path()
        # tools / prompt
        self._tool_defs: list[dict[str, Any]] = list(custom_tools or DEFINITIONS)
        if is_sub_agent:
            # 子 Agent 永不带 agent 工具，防无限嵌套
            self._tool_defs = [t for t in self._tool_defs if t["name"] != "agent"]
        self._custom_system_prompt = custom_system_prompt
        self.system_prompt = custom_system_prompt or self._build_prompt()
        # MCP
        self._mcp = McpManager()
        self._mcp_ready = False
        self._enable_mcp = enable_mcp and not is_sub_agent
        # 子 Agent 输出缓冲
        self._output_buffer: list[str] | None = None

        if self.backend == "anthropic":
            import anthropic

            self._client = anthropic.Anthropic(**client_kwargs)
        else:
            import openai

            self._client = openai.OpenAI(**client_kwargs)
            self.messages.append({"role": "system", "content": self.system_prompt})

    # ─── 对外 API ──────────────────────────────────────────

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self.confirm_fn = fn

    def set_plan_approval_fn(self, fn: PlanApprovalFn) -> None:
        self.plan_approval_fn = fn

    def _build_prompt(self) -> str:
        if self._custom_system_prompt:
            return self._custom_system_prompt
        return build_system_prompt(
            permission_mode=self.permission_mode,
            plan_file_path=self._plan_file_path,
            skills_section=skills_mod.build_skill_descriptions(),
            agents_section=subagent_mod.build_agent_type_section(),
        )

    def abort(self) -> None:
        self._aborted = True

    def _ensure_mcp(self) -> None:
        if not self._enable_mcp or self._mcp_ready:
            return
        self._mcp_ready = True
        try:
            self._mcp.load_and_connect()
            mcp_defs = self._mcp.get_tool_definitions()
            if mcp_defs:
                # 合并 MCP 工具定义
                names = {t["name"] for t in self._tool_defs}
                for d in mcp_defs:
                    if d["name"] not in names:
                        self._tool_defs.append(d)
        except Exception as exc:
            print(f"[mcp] init failed: {exc}", flush=True)

    def _active_tools(self) -> list[dict[str, Any]]:
        return self._tool_defs

    def _estimate_cost_usd(self) -> float:
        # 粗算：input $3/MTok, output $15/MTok（教学用，非账单）
        return (self.total_input_tokens / 1_000_000) * 3 + (
            self.total_output_tokens / 1_000_000
        ) * 15

    def _budget_exceeded(self) -> str | None:
        if self.max_turns is not None and self.turn_count >= self.max_turns:
            return f"Turn limit reached ({self.turn_count} >= {self.max_turns})"
        if self.max_cost_usd is not None and self._estimate_cost_usd() >= self.max_cost_usd:
            return (
                f"Cost limit reached (${self._estimate_cost_usd():.4f} "
                f">= ${self.max_cost_usd})"
            )
        return None

    def close(self) -> None:
        if self._mcp_ready:
            self._mcp.disconnect_all()

    def _refresh_system_prompt(self) -> None:
        self.system_prompt = self._build_prompt()
        if self.backend == "openai" and self.messages:
            if self.messages[0].get("role") == "system":
                self.messages[0]["content"] = self.system_prompt

    def _new_plan_path(self) -> str:
        d = Path.home() / ".qian" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"plan-{int(time.time())}.md"
        if not path.exists():
            path.write_text(
                "# Plan\n\n_Write your step-by-step plan here._\n",
                encoding="utf-8",
            )
        return str(path)

    def chat(self, user_text: str) -> str:
        """一轮用户输入 → 可能多轮 tool 调用 → 返回最终文本。"""
        # Step 10: 关键词召回记忆，附在 user 消息前
        recalled = memory_mod.keyword_recall(user_text)
        if recalled:
            mem_block = memory_mod.format_memories_for_prompt(recalled)
            user_payload = (
                f"<system-reminder>\n{mem_block}\n</system-reminder>\n\n{user_text}"
            )
            print(f"  [memory] recalled {len(recalled)} item(s)")
        else:
            user_payload = user_text

        self.messages.append({"role": "user", "content": user_payload})
        final_parts: list[str] = []
        self._aborted = False
        if not self.is_sub_agent:
            self._ensure_mcp()

        for _ in range(self.max_tool_loops):
            if self._aborted:
                print("[qian] aborted")
                break

            reason = self._budget_exceeded()
            if reason:
                print(f"[budget] {reason}")
                break

            # Step 09: 调用模型前轻量 snip，控制上下文体积
            snipped = maybe_snip_messages(
                self.messages, self.backend, budget_chars=self.snip_char_budget
            )
            if snipped and not self.is_sub_agent:
                print(f"  [context] snipped {snipped} old tool result(s)")

            reply = self._call_model()
            self.messages.append(reply["assistant_message"])
            self.turn_count += 1
            # 粗 token 累计（无 usage 时用字符估算）
            self.total_input_tokens += max(1, estimate_chars(self.messages) // 4)
            self.total_output_tokens += max(1, len(reply.get("text") or "") // 4)

            text = reply.get("text") or ""
            # 流式时已经边下边打；非流式这里整段打印
            if text and not reply.get("streamed"):
                if self._output_buffer is not None:
                    self._output_buffer.append(text)
                else:
                    print(text, flush=True)
            if text:
                final_parts.append(text)
                if reply.get("streamed") and self._output_buffer is None:
                    print(flush=True)  # 流式结束后补换行

            tool_uses = reply.get("tool_uses") or []
            if not tool_uses:
                return "\n".join(final_parts).strip()

            # 预算在 tool 前再查一次
            reason = self._budget_exceeded()
            if reason or self._aborted:
                refuse = reason or "aborted"
                self._append_tool_results(
                    [
                        {
                            "tool_use_id": tu["id"],
                            "name": tu["name"],
                            "content": f"Tool not executed: {refuse}",
                        }
                        for tu in tool_uses
                    ]
                )
                print(f"[budget] stop before tools: {refuse}")
                break

            results = []
            for tu in tool_uses:
                if self._aborted:
                    results.append(
                        {
                            "tool_use_id": tu["id"],
                            "name": tu["name"],
                            "content": "Tool not executed: aborted",
                        }
                    )
                    continue
                if self.verbose_tools:
                    print(f"  → {format_call(tu['name'], tu['input'])}")

                content = self._run_tool_with_permission(tu["name"], tu["input"])
                content = persist_large_result(tu["name"], content)

                if self.verbose_tools:
                    preview = content if len(content) <= 400 else content[:400] + "…"
                    print(f"    ⇐ {preview}")
                results.append(
                    {
                        "tool_use_id": tu["id"],
                        "name": tu["name"],
                        "content": content,
                    }
                )
            self._append_tool_results(results)

        notice = "[stopped: 达到 max_tool_loops 或预算/中断]"
        if not self.is_sub_agent:
            print(notice)
        return ("\n".join(final_parts).strip() + "\n" + notice).strip()

    def _run_tool_with_permission(self, name: str, inp: dict[str, Any]) -> str:
        perm = check_permission(
            name,
            inp,
            self.permission_mode,
            plan_file_path=self._plan_file_path,
        )
        action = perm["action"]

        if action == "deny":
            msg = perm.get("message") or "denied"
            print(f"    ✗ 拒绝: {msg}")
            return f"Action denied: {msg}"

        if action == "confirm":
            key = perm.get("message") or name
            if key not in self._confirmed:
                allowed = False
                if self.confirm_fn is not None:
                    print(f"    ? 需要确认: {key}")
                    allowed = bool(self.confirm_fn(key))
                else:
                    # 非交互环境：没有 confirm_fn 则拒绝（安全默认）
                    print(f"    ✗ 需要确认但无交互: {key}")
                    return f"Action denied (no confirm handler): {key}"
                if not allowed:
                    return "User denied this action."
                self._confirmed.add(key)
                print("    ✓ 已批准（本会话同类不再问）")

        if name in AGENT_SCOPED_TOOLS:
            return self._execute_agent_tool(name, inp)
        if self._mcp.is_mcp_tool(name):
            return self._mcp.call_tool(name, inp)
        return execute(name, inp, self._read_file_state)

    def _execute_agent_tool(self, name: str, inp: dict[str, Any]) -> str:
        if name == "memory_save":
            return memory_mod.tool_memory_save(inp)
        if name == "memory_list":
            return memory_mod.tool_memory_list(inp)
        if name == "memory_get":
            return memory_mod.tool_memory_get(inp)
        if name == "skill":
            return skills_mod.execute_skill(
                str(inp.get("skill_name") or ""),
                str(inp.get("args") or ""),
            )
        if name == "enter_plan_mode":
            return self._enter_plan_mode()
        if name == "exit_plan_mode":
            return self._exit_plan_mode()
        if name == "agent":
            return self._run_sub_agent(inp)
        return f"Error: unhandled agent tool {name}"

    def _run_sub_agent(self, inp: dict[str, Any]) -> str:
        if self.is_sub_agent:
            return "Error: nested sub-agents are not allowed"
        agent_type = str(inp.get("type") or "general")
        description = str(inp.get("description") or "sub-task")
        prompt = str(inp.get("prompt") or "")
        print(f"  [subagent] start {agent_type}: {description}")
        cfg = subagent_mod.get_sub_agent_config(agent_type)
        # 子 Agent 继承权限：plan 模式下只允许 explore/plan 只读类型
        child_mode = self.permission_mode
        if child_mode == "plan" and agent_type == "general":
            return "Error: general sub-agent blocked in plan mode; use explore/plan"
        sub = Agent(
            model=self.model,
            max_tool_loops=min(12, self.max_tool_loops),
            stream=False,
            permission_mode=child_mode if child_mode != "plan" else "default",
            is_sub_agent=True,
            custom_system_prompt=cfg["system_prompt"],
            custom_tools=cfg["tools"],
            enable_mcp=False,
            max_turns=self.max_turns,
        )
        # plan 文件可写权限不传给子代理
        try:
            result = sub.run_once(prompt)
            self.total_input_tokens += result.get("input_tokens", 0)
            self.total_output_tokens += result.get("output_tokens", 0)
            print(f"  [subagent] end {agent_type}: {description}")
            return result.get("text") or "(sub-agent produced no output)"
        except Exception as exc:
            print(f"  [subagent] error: {exc}")
            return f"Sub-agent error: {type(exc).__name__}: {exc}"

    def run_once(self, prompt: str) -> dict[str, Any]:
        """子 Agent 入口：捕获输出文本。"""
        self._output_buffer = []
        prev_in, prev_out = self.total_input_tokens, self.total_output_tokens
        # 子代理不注入记忆，避免噪音
        self.messages.append({"role": "user", "content": prompt})
        final_parts: list[str] = []
        self._aborted = False
        for _ in range(self.max_tool_loops):
            if self._aborted:
                break
            reply = self._call_model()
            self.messages.append(reply["assistant_message"])
            self.turn_count += 1
            text = reply.get("text") or ""
            if text:
                self._output_buffer.append(text)
                final_parts.append(text)
            tool_uses = reply.get("tool_uses") or []
            if not tool_uses:
                break
            results = []
            for tu in tool_uses:
                content = self._run_tool_with_permission(tu["name"], tu["input"])
                content = persist_large_result(tu["name"], content)
                results.append(
                    {
                        "tool_use_id": tu["id"],
                        "name": tu["name"],
                        "content": content,
                    }
                )
            self._append_tool_results(results)
        self._output_buffer = None
        return {
            "text": "\n".join(final_parts).strip(),
            "input_tokens": self.total_input_tokens - prev_in,
            "output_tokens": self.total_output_tokens - prev_out,
        }

    def _enter_plan_mode(self) -> str:
        if self.permission_mode == "plan":
            return f"Already in plan mode. Plan file: {self._plan_file_path}"
        self._pre_plan_mode = self.permission_mode
        self.permission_mode = "plan"
        self._plan_file_path = self._new_plan_path()
        self._refresh_system_prompt()
        return (
            f"Entered plan mode (read-only). Write your plan ONLY to:\n"
            f"{self._plan_file_path}\n"
            f"When done, call exit_plan_mode."
        )

    def _exit_plan_mode(self) -> str:
        if self.permission_mode != "plan":
            return "Not in plan mode."
        plan_path = self._plan_file_path
        plan_text = ""
        if plan_path and Path(plan_path).exists():
            plan_text = Path(plan_path).read_text(encoding="utf-8")
        else:
            plan_text = "(empty plan file)"

        choice = "execute"
        feedback = None
        if self.plan_approval_fn is not None:
            result = self.plan_approval_fn(plan_text)
            choice = str(result.get("choice") or "execute")
            feedback = result.get("feedback")
        else:
            print("\n===== PLAN =====")
            print(plan_text[:4000])
            print("===== END PLAN (no approval fn → auto execute) =====\n")

        if choice == "keep-planning":
            note = f"User wants more planning. Feedback: {feedback or '(none)'}"
            return note

        if choice == "abort":
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._refresh_system_prompt()
            return "Plan aborted. Back to previous permission mode."

        # execute or clear-and-execute
        self.permission_mode = self._pre_plan_mode or "default"
        self._pre_plan_mode = None
        cleared = ""
        if choice == "clear-and-execute":
            self.clear_history()
            cleared = " Conversation history cleared."
        self._refresh_system_prompt()
        return (
            f"Plan approved ({choice}). Left plan mode → {self.permission_mode}.{cleared}\n"
            f"Plan file: {plan_path}\n"
            f"Now implement the plan."
        )

    def toggle_plan_mode(self) -> str:
        if self.permission_mode == "plan":
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            path = self._plan_file_path
            self._plan_file_path = None
            self._refresh_system_prompt()
            return f"Exited plan mode → {self.permission_mode} (plan was {path})"
        return self._enter_plan_mode()

    def clear_history(self) -> None:
        self.messages = []
        self.turn_count = 0
        self._read_file_state.clear()
        if self.backend == "openai":
            self.messages.append({"role": "system", "content": self.system_prompt})

    def export_messages(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def import_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = list(messages)
        if self.backend == "openai":
            if not self.messages or self.messages[0].get("role") != "system":
                self.messages.insert(0, {"role": "system", "content": self.system_prompt})

    def context_stats(self) -> dict[str, Any]:
        chars = estimate_chars(self.messages)
        return {
            "messages": len(self.messages),
            "chars": chars,
            "snip_budget": self.snip_char_budget,
            "compact_count": self.compact_count,
            "read_files_tracked": len(self._read_file_state),
            "turns": self.turn_count,
            "est_cost_usd": round(self._estimate_cost_usd(), 6),
            "max_turns": self.max_turns,
            "max_cost_usd": self.max_cost_usd,
            "tools": len(self._tool_defs),
        }

    def compact(self) -> str:
        """Step 09: 用模型把历史压成摘要，替换 messages（保留 system）。"""
        if len(self.messages) <= 2:
            print("[context] 历史太短，无需 compact")
            return "noop"

        print("[context] compacting conversation…")
        summary = summarize_for_compact(
            self.messages,
            backend=self.backend,
            client=self._client,
            model=self.model,
            system_prompt=self.system_prompt,
        )
        self.messages = []
        if self.backend == "openai":
            self.messages.append({"role": "system", "content": self.system_prompt})
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "<system-reminder>Conversation was compacted. "
                        f"Summary:\n{summary}</system-reminder>"
                    ),
                }
            )
            self.messages.append(
                {
                    "role": "assistant",
                    "content": "Understood. I will continue from the compact summary.",
                }
            )
        else:
            # Anthropic: system 在 API 字段，不在 messages
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "<system-reminder>Conversation was compacted. "
                        f"Summary:\n{summary}</system-reminder>"
                    ),
                }
            )
            self.messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Understood. I will continue from the compact summary.",
                        }
                    ],
                }
            )
        self.compact_count += 1
        # compact 后 mtime 状态仍保留（文件事实不变）
        print(f"[context] compact done (#{self.compact_count}), chars≈{estimate_chars(self.messages)}")
        return summary

    # ─── 模型调用（Step 05 流式） ───────────────────────────

    def _call_model(self) -> dict[str, Any]:
        if self.backend == "anthropic":
            return (
                self._call_anthropic_stream()
                if self.stream
                else self._call_anthropic()
            )
        return self._call_openai_stream() if self.stream else self._call_openai()

    def _call_anthropic(self) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            tools=self._active_tools(),
            messages=self.messages,
        )
        return self._parse_anthropic_message(response, streamed=False)

    def _call_anthropic_stream(self) -> dict[str, Any]:
        text_parts: list[str] = []
        with self._client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            tools=self._active_tools(),
            messages=self.messages,
        ) as stream:
            for delta in stream.text_stream:
                if self._output_buffer is not None:
                    self._output_buffer.append(delta)
                else:
                    print(delta, end="", flush=True)
                text_parts.append(delta)
            response = stream.get_final_message()
        # text 已打印；仍从 final message 解析 tool_use，保证与 API 一致
        parsed = self._parse_anthropic_message(response, streamed=True)
        # 若 stream 只吐了 text 而 final 结构异常，用已打印文本兜底
        if not parsed["text"] and text_parts:
            parsed["text"] = "".join(text_parts)
        return parsed

    def _parse_anthropic_message(self, response: Any, *, streamed: bool) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        content_for_history: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                content_for_history.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                args = (
                    dict(block.input)
                    if hasattr(block.input, "items")
                    else block.input
                )
                tool_uses.append({"id": block.id, "name": block.name, "input": args})
                content_for_history.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": args,
                    }
                )

        return {
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "streamed": streamed,
            "assistant_message": {"role": "assistant", "content": content_for_history},
        }

    def _call_openai(self) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=self.messages,
            tools=to_openai_tools(self._active_tools()),
        )
        return self._parse_openai_message(response.choices[0].message, streamed=False)

    def _call_openai_stream(self) -> dict[str, Any]:
        """拼 OpenAI stream delta → 与非流式相同的 assistant message 形状。"""
        stream = self._client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=self.messages,
            tools=to_openai_tools(self._active_tools()),
            stream=True,
        )

        text_parts: list[str] = []
        # index → {id, name, arguments}
        tool_acc: dict[int, dict[str, str]] = {}

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            if delta.content:
                if self._output_buffer is not None:
                    self._output_buffer.append(delta.content)
                else:
                    print(delta.content, end="", flush=True)
                text_parts.append(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    slot = tool_acc.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

        # 组装假 message 对象字段，复用解析逻辑
        class _Fn:
            def __init__(self, name: str, arguments: str) -> None:
                self.name = name
                self.arguments = arguments

        class _Tc:
            def __init__(self, id: str, name: str, arguments: str) -> None:
                self.id = id
                self.function = _Fn(name, arguments)

        class _Msg:
            def __init__(self) -> None:
                self.content = "".join(text_parts) or None
                self.tool_calls = None

        msg = _Msg()
        if tool_acc:
            msg.tool_calls = [
                _Tc(v["id"] or f"call_{i}", v["name"], v["arguments"] or "{}")
                for i, v in sorted(tool_acc.items())
            ]

        return self._parse_openai_message(msg, streamed=True)

    def _parse_openai_message(self, msg: Any, *, streamed: bool) -> dict[str, Any]:
        text = msg.content or ""
        tool_uses: list[dict[str, Any]] = []
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": text or None,
        }

        if msg.tool_calls:
            assistant_message["tool_calls"] = []
            for tc in msg.tool_calls:
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
                tool_uses.append(
                    {"id": tc.id, "name": tc.function.name, "input": args}
                )
                assistant_message["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": raw_args,
                        },
                    }
                )

        return {
            "text": text,
            "tool_uses": tool_uses,
            "streamed": streamed,
            "assistant_message": assistant_message,
        }

    def _append_tool_results(self, results: list[dict[str, Any]]) -> None:
        if self.backend == "anthropic":
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r["tool_use_id"],
                            "content": r["content"],
                        }
                        for r in results
                    ],
                }
            )
        else:
            for r in results:
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": r["tool_use_id"],
                        "content": r["content"],
                    }
                )
