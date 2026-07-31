"""Step 11 自测。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qian.skills import (  # noqa: E402
    discover_skills,
    execute_skill,
    get_skill_by_name,
    reset_skill_cache,
)


def main() -> None:
    reset_skill_cache()
    skill_dir = ROOT / ".qian" / "skills" / "greet"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Say hello\nuser-invocable: true\n---\n\nHello $ARGUMENTS!\n",
        encoding="utf-8",
    )
    reset_skill_cache()
    # discover from cwd
    import os

    os.chdir(ROOT)
    reset_skill_cache()
    s = get_skill_by_name("greet")
    assert s is not None, discover_skills()
    out = execute_skill("greet", "Qian")
    assert "Hello Qian!" in out
    print("ALL PASS")


if __name__ == "__main__":
    main()
