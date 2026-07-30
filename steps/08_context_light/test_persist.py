"""Step 08 本地自测（不调模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.context import LARGE_RESULT_BYTES, persist_large_result  # noqa: E402


def main() -> None:
    small = persist_large_result("echo", "hi")
    assert small == "hi", small
    print("ok: small unchanged")

    big = "x" * (LARGE_RESULT_BYTES + 1000)
    out = persist_large_result("run_shell", big)
    assert "Result too large" in out
    assert "Preview" in out
    assert "tool-results" in out
    print("ok: large persisted")
    print("ALL PASS")


if __name__ == "__main__":
    main()
