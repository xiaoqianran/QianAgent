"""Step 17 自测。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.tools import CONCURRENCY_SAFE_TOOLS, is_concurrency_safe  # noqa: E402


def main() -> None:
    assert is_concurrency_safe("read_file")
    assert is_concurrency_safe("list_files")
    assert not is_concurrency_safe("write_file")
    assert not is_concurrency_safe("run_shell")
    assert not is_concurrency_safe("agent")
    assert "memory_get" in CONCURRENCY_SAFE_TOOLS
    print("ALL PASS")


if __name__ == "__main__":
    main()
