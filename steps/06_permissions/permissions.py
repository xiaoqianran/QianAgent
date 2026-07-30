"""Step 06 最小切片：与 qian.permissions 同源逻辑，便于单独阅读。"""

from __future__ import annotations

# 直接复用累计包，避免两份漂移
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.permissions import check_permission, is_dangerous_command  # noqa: E402,F401

if __name__ == "__main__":
    # 本地自测，不调模型
    cases = [
        ("read_file", {"file_path": "a.py"}, "default"),
        ("write_file", {"file_path": "/tmp/qian_new_file_test.txt"}, "default"),
        ("run_shell", {"command": "ls"}, "default"),
        ("run_shell", {"command": "rm -rf /"}, "default"),
        ("run_shell", {"command": "rm -rf /"}, "bypass"),
        ("run_shell", {"command": "rm -rf /"}, "dontAsk"),
        ("write_file", {"file_path": "x"}, "plan"),
    ]
    for name, inp, mode in cases:
        print(mode, name, check_permission(name, inp, mode))
