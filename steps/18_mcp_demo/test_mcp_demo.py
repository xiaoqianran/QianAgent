"""Step 18：真连 demo MCP server（本地子进程）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.mcp_client import McpManager  # noqa: E402


def main() -> None:
    server = ROOT / "examples" / "mcp_demo_server.py"
    assert server.exists(), server

    # 写临时 settings 到 cwd 可见路径：用环境隔离 —— 直接注入 manager 配置更稳
    # 这里通过临时 .qian/settings.json 在 ROOT 下写入
    qian_dir = ROOT / ".qian"
    qian_dir.mkdir(exist_ok=True)
    settings = qian_dir / "settings.json"
    backup = None
    if settings.exists():
        backup = settings.read_text(encoding="utf-8")
    settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": sys.executable,
                        "args": [str(server)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    old = os.getcwd()
    try:
        os.chdir(ROOT)
        m = McpManager()
        m.load_and_connect()
        defs = m.get_tool_definitions()
        names = {d["name"] for d in defs}
        assert "mcp__demo__echo" in names, names
        assert "mcp__demo__add" in names, names
        out = m.call_tool("mcp__demo__echo", {"text": "ping"})
        assert out == "ping", out
        out2 = m.call_tool("mcp__demo__add", {"a": 2, "b": 3})
        assert float(out2.strip()) == 5.0, out2
        m.disconnect_all()
        print("ALL PASS")
    finally:
        os.chdir(old)
        if backup is None:
            settings.unlink(missing_ok=True)
        else:
            settings.write_text(backup, encoding="utf-8")


if __name__ == "__main__":
    main()
