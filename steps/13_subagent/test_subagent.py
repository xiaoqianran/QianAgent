"""Step 13 自测（不调模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.subagent import get_sub_agent_config  # noqa: E402


def main() -> None:
    ex = get_sub_agent_config("explore")
    names = {t["name"] for t in ex["tools"]}
    assert "read_file" in names
    assert "write_file" not in names
    assert "agent" not in names

    gen = get_sub_agent_config("general")
    gnames = {t["name"] for t in gen["tools"]}
    assert "write_file" in gnames
    assert "agent" not in gnames
    print("ALL PASS")


if __name__ == "__main__":
    main()
