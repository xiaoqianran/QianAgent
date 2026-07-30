"""Step 01: 最小 Agent Loop —— 只有 messages + while + 调模型。

故意不引入工具、权限、压缩。读懂这一文件，就读懂了整个 agent 的心脏。
"""

from __future__ import annotations

import os
from typing import Any


# ─── 后端探测 ───────────────────────────────────────────────


def detect_backend() -> tuple[str, dict[str, Any]]:
    """返回 (backend, client_kwargs)。

    - anthropic: 有 ANTHROPIC_API_KEY
    - openai:    有 OPENAI_API_KEY（可用 OPENAI_BASE_URL）
    """
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


# ─── Agent ─────────────────────────────────────────────────


SYSTEM_PROMPT = (
    "你是 QianAgent（第 1 步：只有对话循环）。"
    "现在还没有工具，只能用文字回答。回答简短。"
)


class Agent:
    """一步一个用户回合：调模型直到它不再要工具。

    Step 01 没有真实工具，tools 恒为空列表。
    循环结构已经是最终形态——后面步骤只往里「塞东西」。
    """

    def __init__(self, model: str | None = None) -> None:
        self.backend, client_kwargs = detect_backend()
        self.model = model or default_model(self.backend)
        self.messages: list[dict[str, Any]] = []

        if self.backend == "anthropic":
            import anthropic

            self._client = anthropic.Anthropic(**client_kwargs)
        else:
            import openai

            self._client = openai.OpenAI(**client_kwargs)
            # OpenAI 把 system 放在 messages 里
            self.messages.append({"role": "system", "content": SYSTEM_PROMPT})

    def chat(self, user_text: str) -> str:
        """处理一轮用户输入，返回助手最终文本。"""
        self.messages.append({"role": "user", "content": user_text})
        final_text_parts: list[str] = []

        while True:
            reply = self._call_model()
            # 把助手回复记入历史（含 tool_use，若有）
            self.messages.append(reply["assistant_message"])

            text = reply.get("text") or ""
            if text:
                print(text, flush=True)
                final_text_parts.append(text)

            tool_uses = reply.get("tool_uses") or []
            if not tool_uses:
                # 模型不再要工具 → 本轮结束
                return "\n".join(final_text_parts).strip()

            # Step 01：还没有执行器。若模型乱调工具，回一条错误继续。
            tool_results = []
            for tu in tool_uses:
                print(f"  → [step01 无工具] {tu['name']}({tu['input']})")
                tool_results.append(
                    {
                        "tool_use_id": tu["id"],
                        "name": tu["name"],
                        "content": "Error: Step 01 尚未注册任何工具。",
                    }
                )
            self._append_tool_results(tool_results)

    def _call_model(self) -> dict[str, Any]:
        if self.backend == "anthropic":
            return self._call_anthropic()
        return self._call_openai()

    def _call_anthropic(self) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[],  # Step 01: 空
            messages=self.messages,
        )
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        content_for_history: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                content_for_history.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_uses.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input)
                        if hasattr(block.input, "items")
                        else block.input,
                    }
                )
                content_for_history.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input)
                        if hasattr(block.input, "items")
                        else block.input,
                    }
                )

        return {
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "assistant_message": {"role": "assistant", "content": content_for_history},
        }

    def _call_openai(self) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=self.messages,
            tools=[],  # Step 01: 空
        )
        msg = response.choices[0].message
        text = msg.content or ""
        tool_uses: list[dict[str, Any]] = []

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": text or None,
        }

        if msg.tool_calls:
            assistant_message["tool_calls"] = []
            for tc in msg.tool_calls:
                import json

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
