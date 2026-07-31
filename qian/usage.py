"""Token / 费用统计（Step 16）。

优先用 API 返回的 usage；拿不到时再回退字符估算。
费率表仅作教学粗估，不是真实账单。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# model 子串 → (input_$/MTok, output_$/MTok)
# 找不到时用默认 Claude 级粗价
DEFAULT_RATES = (3.0, 15.0)

MODEL_RATES: list[tuple[str, tuple[float, float]]] = [
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.5, 10.0)),
    ("gpt-4.1", (2.0, 8.0)),
    ("o4-mini", (1.1, 4.4)),
    ("claude-haiku", (0.8, 4.0)),
    ("claude-sonnet", (3.0, 15.0)),
    ("claude-opus", (15.0, 75.0)),
    ("gpt-oss", (0.5, 1.5)),  # 兼容中转常见开源大模型占位
]


def rates_for_model(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for key, rates in MODEL_RATES:
        if key in m:
            return rates
    return DEFAULT_RATES


@dataclass
class UsageDelta:
    input_tokens: int = 0
    output_tokens: int = 0
    from_api: bool = False


def extract_usage_anthropic(response: Any) -> UsageDelta:
    usage = getattr(response, "usage", None)
    if usage is None:
        return UsageDelta()
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    # cache tokens 也算进「已处理输入」口径的一部分（教学简化：并入 input）
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_create = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    if inp or out or cache_read or cache_create:
        return UsageDelta(
            input_tokens=inp + cache_read + cache_create,
            output_tokens=out,
            from_api=True,
        )
    return UsageDelta()


def extract_usage_openai(response: Any) -> UsageDelta:
    usage = getattr(response, "usage", None)
    if usage is None:
        return UsageDelta()
    inp = int(getattr(usage, "prompt_tokens", 0) or 0)
    out = int(getattr(usage, "completion_tokens", 0) or 0)
    if inp or out:
        return UsageDelta(input_tokens=inp, output_tokens=out, from_api=True)
    return UsageDelta()


def estimate_usage_from_text(*, messages_chars: int, output_text: str) -> UsageDelta:
    return UsageDelta(
        input_tokens=max(1, messages_chars // 4),
        output_tokens=max(1, len(output_text or "") // 4),
        from_api=False,
    )


def cost_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    rin, rout = rates_for_model(model)
    return (input_tokens / 1_000_000) * rin + (output_tokens / 1_000_000) * rout
