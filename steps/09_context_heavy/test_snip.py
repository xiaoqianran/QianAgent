"""Step 09 snip 自测（不调模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.context import SNIP_PLACEHOLDER, estimate_chars, maybe_snip_messages  # noqa: E402


def main() -> None:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(10):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": ("payload-" + str(i) + "-") * 5000,
            }
        )
    before = estimate_chars(messages)
    n = maybe_snip_messages(messages, "openai", budget_chars=5_000, keep_recent=3)
    assert n > 0, n
    snipped = sum(1 for m in messages if m.get("content") == SNIP_PLACEHOLDER)
    assert snipped == n
    after = estimate_chars(messages)
    assert after < before
    print(f"ok: snipped {n}, chars {before} -> {after}")
    print("ALL PASS")


if __name__ == "__main__":
    main()
