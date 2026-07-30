"""上下文管理（Step 08 轻量 + Step 09 snip/compact）。

分层（由轻到重）：
1. persist_large_result  — 单次 tool 结果 > 阈值 → 落盘，上下文只留预览
2. maybe_snip_messages   — 会话总字符超预算 → 把旧 tool_result 换成占位符
3. summarize_for_compact — 手动/主动摘要，整段历史换成 summary
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

# ─── Step 08: 大结果落盘 ───────────────────────────────────

LARGE_RESULT_BYTES = 30 * 1024  # 30 KB
TOOL_RESULT_DIR = Path.home() / ".qian" / "tool-results"
SNIP_PLACEHOLDER = "[Content snipped — re-read with read_file if needed]"
KEEP_RECENT_TOOL_RESULTS = 4


def persist_large_result(tool_name: str, result: str) -> str:
    """超大 tool 结果写到磁盘，返回短预览。信息可再 read_file 取回。"""
    raw = result.encode("utf-8", errors="replace")
    if len(raw) <= LARGE_RESULT_BYTES:
        return result

    TOOL_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}-{tool_name}.txt"
    path = TOOL_RESULT_DIR / filename
    path.write_text(result, encoding="utf-8")

    lines = result.splitlines()
    preview = "\n".join(lines[:120])
    size_kb = len(raw) / 1024
    return (
        f"[Result too large ({size_kb:.1f} KB, {len(lines)} lines). "
        f"Full output saved to {path}. "
        f"Use read_file on that path if you need more.]\n\n"
        f"Preview (first 120 lines):\n{preview}"
    )


def head_tail_truncate(text: str, max_chars: int = 50_000) -> str:
    if len(text) <= max_chars:
        return text
    keep = (max_chars - 80) // 2
    return (
        text[:keep]
        + f"\n\n[... truncated {len(text) - keep * 2} chars ...]\n\n"
        + text[-keep:]
    )


# ─── 估算 ─────────────────────────────────────────────────


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += len(json.dumps(content, ensure_ascii=False, default=str))
        else:
            total += len(str(content))
        if m.get("tool_calls"):
            total += len(json.dumps(m["tool_calls"], ensure_ascii=False, default=str))
    return total


# ─── Step 09: snip 旧 tool_result ──────────────────────────


def _iter_tool_result_slots(
    messages: list[dict[str, Any]], backend: str
) -> list[tuple[int, Any]]:
    """返回可变引用列表: (message_index, slot) 其中 slot 可写 content。

    OpenAI: messages[i] role=tool → slot 是整个 message dict
    Anthropic: messages[i] role=user content=list → slot 是 tool_result block dict
    """
    slots: list[tuple[int, Any]] = []
    if backend == "openai":
        for i, m in enumerate(messages):
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                if m["content"] != SNIP_PLACEHOLDER:
                    slots.append((i, m))
    else:
        for i, m in enumerate(messages):
            if m.get("role") != "user" or not isinstance(m.get("content"), list):
                continue
            for block in m["content"]:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and isinstance(block.get("content"), str)
                    and block["content"] != SNIP_PLACEHOLDER
                ):
                    slots.append((i, block))
    return slots


def maybe_snip_messages(
    messages: list[dict[str, Any]],
    backend: str,
    *,
    budget_chars: int = 120_000,
    keep_recent: int = KEEP_RECENT_TOOL_RESULTS,
) -> int:
    """若总字符超预算，把较旧的 tool_result 换成占位符。返回 snip 条数。"""
    if estimate_chars(messages) < budget_chars:
        return 0

    slots = _iter_tool_result_slots(messages, backend)
    if len(slots) <= keep_recent:
        # 仍超预算：对保留的也做头尾截断
        for _, slot in slots:
            if backend == "openai":
                slot["content"] = head_tail_truncate(slot["content"], 12_000)
            else:
                slot["content"] = head_tail_truncate(slot["content"], 12_000)
        return 0

    snip_count = len(slots) - keep_recent
    for i in range(snip_count):
        _, slot = slots[i]
        if backend == "openai":
            slot["content"] = SNIP_PLACEHOLDER
        else:
            slot["content"] = SNIP_PLACEHOLDER
    return snip_count


# ─── Step 09: full compact ─────────────────────────────────


def _flatten_messages_for_summary(messages: list[dict[str, Any]], backend: str) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        if role == "system":
            continue
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            bits = []
            for b in content:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        bits.append(str(b.get("text", "")))
                    elif b.get("type") == "tool_use":
                        bits.append(f"[tool_use {b.get('name')}]")
                    elif b.get("type") == "tool_result":
                        c = str(b.get("content", ""))
                        bits.append(f"[tool_result {c[:200]}]")
            text = "\n".join(bits)
        else:
            text = str(content)
        if m.get("tool_calls"):
            names = [
                tc.get("function", {}).get("name", "?")
                for tc in m["tool_calls"]
            ]
            text = (text or "") + f"\n[tool_calls: {', '.join(names)}]"
        if role == "tool":
            parts.append(f"tool: {text[:500]}")
        else:
            parts.append(f"{role}: {text[:1500]}")
    joined = "\n\n".join(parts)
    return head_tail_truncate(joined, 60_000)


def summarize_for_compact(
    messages: list[dict[str, Any]],
    *,
    backend: str,
    client: Any,
    model: str,
    system_prompt: str,
) -> str:
    """调用模型生成压缩摘要（无 tools）。"""
    transcript = _flatten_messages_for_summary(messages, backend)
    instruction = (
        "Summarize this coding-agent conversation for continuity. Include: "
        "user goals, key files touched, decisions, errors, and next steps. "
        "Be concise but complete. Use the same language as the user when possible.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    if backend == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system="You compress agent transcripts into durable summaries.",
            messages=[{"role": "user", "content": instruction}],
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip() or "(empty summary)"

    resp = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[
            {
                "role": "system",
                "content": "You compress agent transcripts into durable summaries.",
            },
            {"role": "user", "content": instruction},
        ],
    )
    return (resp.choices[0].message.content or "").strip() or "(empty summary)"
