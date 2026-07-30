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
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from .context import (
    estimate_chars,
    maybe_snip_messages,
    persist_large_result,
    summarize_for_compact,
)
from .permissions import PermissionMode, check_permission
from .prompt import build_system_prompt
from .tools import DEFINITIONS, execute, format_call, to_openai_tools

# confirm_fn(message) -> bool
ConfirmFn = Callable[[str], bool]

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
    ) -> None:
        self.backend, client_kwargs = detect_backend()
        self.model = model or default_model(self.backend)
        self.max_tool_loops = max_tool_loops
        self.verbose_tools = verbose_tools
        self.stream = stream
        self.permission_mode = permission_mode
        self.confirm_fn = confirm_fn
        # 同类 confirm 只问一次（用 message 字符串当 key）
        self._confirmed: set[str] = set()
        # Step 07: abs_path → mtime
        self._read_file_state: dict[str, float] = {}
        self.messages: list[dict[str, Any]] = []
        self.turn_count = 0
        self.snip_char_budget = DEFAULT_SNIP_CHAR_BUDGET
        self.compact_count = 0
        self.system_prompt = build_system_prompt(permission_mode=permission_mode)

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

    def chat(self, user_text: str) -> str:
        """一轮用户输入 → 可能多轮 tool 调用 → 返回最终文本。"""
        self.messages.append({"role": "user", "content": user_text})
        final_parts: list[str] = []

        for _ in range(self.max_tool_loops):
            # Step 09: 调用模型前轻量 snip，控制上下文体积
            snipped = maybe_snip_messages(
                self.messages, self.backend, budget_chars=self.snip_char_budget
            )
            if snipped:
                print(f"  [context] snipped {snipped} old tool result(s)")

            reply = self._call_model()
            self.messages.append(reply["assistant_message"])
            self.turn_count += 1

            text = reply.get("text") or ""
            # 流式时已经边下边打；非流式这里整段打印
            if text and not reply.get("streamed"):
                print(text, flush=True)
            if text:
                final_parts.append(text)
                if reply.get("streamed"):
                    print(flush=True)  # 流式结束后补换行

            tool_uses = reply.get("tool_uses") or []
            if not tool_uses:
                return "\n".join(final_parts).strip()

            results = []
            for tu in tool_uses:
                if self.verbose_tools:
                    print(f"  → {format_call(tu['name'], tu['input'])}")

                # Step 06 权限 → Step 07 mtime 在 execute 内 → Step 08 大结果落盘
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

        notice = "[stopped: 达到 max_tool_loops]"
        print(notice)
        return ("\n".join(final_parts).strip() + "\n" + notice).strip()

    def _run_tool_with_permission(self, name: str, inp: dict[str, Any]) -> str:
        perm = check_permission(name, inp, self.permission_mode)
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

        return execute(name, inp, self._read_file_state)

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
            tools=DEFINITIONS,
            messages=self.messages,
        )
        return self._parse_anthropic_message(response, streamed=False)

    def _call_anthropic_stream(self) -> dict[str, Any]:
        text_parts: list[str] = []
        with self._client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            tools=DEFINITIONS,
            messages=self.messages,
        ) as stream:
            for delta in stream.text_stream:
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
            tools=to_openai_tools(),
        )
        return self._parse_openai_message(response.choices[0].message, streamed=False)

    def _call_openai_stream(self) -> dict[str, Any]:
        """拼 OpenAI stream delta → 与非流式相同的 assistant message 形状。"""
        stream = self._client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=self.messages,
            tools=to_openai_tools(),
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
