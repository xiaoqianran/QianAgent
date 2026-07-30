"""Step 02: 在 Step 01 的循环上挂上真实工具。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# 同目录 tools
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import DEFINITIONS, execute, to_openai_tools  # noqa: E402


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
    raise RuntimeError("需要 ANTHROPIC_API_KEY 或 OPENAI_API_KEY")


def default_model(backend: str) -> str:
    return os.environ.get("QIAN_MODEL") or (
        "claude-sonnet-4-6" if backend == "anthropic" else "gpt-4o"
    )


SYSTEM_PROMPT = (
    "你是 QianAgent（第 2 步：已有文件与 shell 工具）。"
    "需要看文件时用 read_file，改文件用 edit_file/write_file，跑命令用 run_shell。"
    "回答简洁，先做事再总结。"
)


class Agent:
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
            self.messages.append({"role": "system", "content": SYSTEM_PROMPT})

    def chat(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        final_parts: list[str] = []

        # 防止模型死循环狂调工具
        for _ in range(30):
            reply = self._call_model()
            self.messages.append(reply["assistant_message"])

            text = reply.get("text") or ""
            if text:
                print(text, flush=True)
                final_parts.append(text)

            tool_uses = reply.get("tool_uses") or []
            if not tool_uses:
                return "\n".join(final_parts).strip()

            results = []
            for tu in tool_uses:
                print(f"  → {tu['name']}({json.dumps(tu['input'], ensure_ascii=False)})")
                content = execute(tu["name"], tu["input"])
                # 控制台只预览前 400 字
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

        return "\n".join(final_parts).strip() + "\n[stopped: max tool loops]"

    def _call_model(self) -> dict[str, Any]:
        if self.backend == "anthropic":
            return self._call_anthropic()
        return self._call_openai()

    def _call_anthropic(self) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=DEFINITIONS,
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
            "assistant_message": {"role": "assistant", "content": content_for_history},
        }

    def _call_openai(self) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=self.messages,
            tools=to_openai_tools(),
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
