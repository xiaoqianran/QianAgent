"""Step 10 自测（不调模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian import memory as mem  # noqa: E402


def main() -> None:
    name = "qian_test_pref"
    fn = mem.save_memory(name, "test preference", "project", "Prefer Chinese comments.")
    assert fn.endswith(".md")
    listed = mem.tool_memory_list({})
    assert name in listed or fn in listed
    got = mem.get_memory(fn)
    assert got and "Chinese" in got.content
    recalled = mem.keyword_recall("Chinese comments preference")
    assert any(r.name == name for r in recalled)
    assert mem.delete_memory(fn)
    print("ALL PASS", mem.get_memory_dir())


if __name__ == "__main__":
    main()
