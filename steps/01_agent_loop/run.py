#!/usr/bin/env python3
"""运行 Step 01。用法: python run.py "你好" """

from __future__ import annotations

import sys
from pathlib import Path

# 允许直接 python steps/01_agent_loop/run.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "MokioAgent" / ".env")

from agent import Agent


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or "用一句话介绍你自己"
    print(f"[step01] user: {prompt}\n")
    agent = Agent()
    print(f"[step01] backend={agent.backend} model={agent.model}\n")
    agent.chat(prompt)
    print()


if __name__ == "__main__":
    main()
