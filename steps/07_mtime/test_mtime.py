"""Step 07 本地自测（不调模型）。"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.tools import execute  # noqa: E402


def main() -> None:
    state: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "demo.txt"
        path.write_text("hello\n", encoding="utf-8")

        # 未读就 edit → 失败
        r = execute("edit_file", {"file_path": str(path), "old_string": "hello", "new_string": "hi"}, state)
        assert "must read" in r.lower() or "先" in r, r
        print("ok: block edit without read")

        # 读后 edit → 成功
        r = execute("read_file", {"file_path": str(path)}, state)
        assert "hello" in r
        r = execute("edit_file", {"file_path": str(path), "old_string": "hello", "new_string": "hi"}, state)
        assert r.startswith("Edited"), r
        print("ok: edit after read")

        # 外部修改后 edit → 失败
        time.sleep(0.02)
        path.write_text("external\n", encoding="utf-8")
        r = execute(
            "edit_file",
            {"file_path": str(path), "old_string": "external", "new_string": "x"},
            state,
        )
        assert "modified" in r.lower() or "外部" in r or "重读" in r, r
        print("ok: block stale edit")

        # 新建 write 无需 read
        newp = Path(tmp) / "new.txt"
        r = execute("write_file", {"file_path": str(newp), "content": "n"}, state)
        assert r.startswith("Wrote"), r
        print("ok: write new file without read")

    print("ALL PASS")


if __name__ == "__main__":
    main()
