#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "MokioAgent" / ".env")

from agent import Agent


def main() -> None:
    # 默认任务：读项目 README，证明工具环通了
    default = "读取 ../../README.md 的前 30 行，用三句话总结 QianAgent 是什么"
    prompt = " ".join(sys.argv[1:]).strip() or default
    print(f"[step02] user: {prompt}\n")
    agent = Agent()
    print(f"[step02] backend={agent.backend} model={agent.model}\n")
    # 在 steps/02_tools 下跑时，相对路径以 cwd 为准；建议从 QianAgent 根目录调用
    agent.chat(prompt)
    print()


if __name__ == "__main__":
    main()
