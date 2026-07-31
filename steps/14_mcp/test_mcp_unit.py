"""Step 14 配置加载自测（不启真 server）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.mcp_client import McpManager  # noqa: E402


def main() -> None:
    m = McpManager()
    # 无配置时 connect 应安静成功
    m.load_and_connect()
    assert m.get_tool_definitions() == []
    assert not m.is_mcp_tool("read_file")
    assert m.is_mcp_tool("mcp__x__y")
    print("ALL PASS")


if __name__ == "__main__":
    main()
