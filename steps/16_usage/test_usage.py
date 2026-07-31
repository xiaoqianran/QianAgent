"""Step 16 自测。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.usage import (  # noqa: E402
    cost_usd,
    extract_usage_openai,
    rates_for_model,
)


def main() -> None:
    assert rates_for_model("gpt-4o-mini")[0] < rates_for_model("claude-opus")[0]
    u = extract_usage_openai(
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))
    )
    assert u.from_api and u.input_tokens == 100 and u.output_tokens == 50
    c = cost_usd(1_000_000, 0, "gpt-4o-mini")
    assert abs(c - 0.15) < 1e-9
    print("ALL PASS")


if __name__ == "__main__":
    main()
