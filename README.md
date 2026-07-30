# QianAgent

从零手写的 **Coding Agent**。架构对齐 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 的 Python 版（`mini_claude`），但**完全独立重写**，按最小步骤一层层搭起来。

## 设计原则

1. **架构一眼能看懂**：扁平模块，不搞深层分包。
2. **一步一个概念**：每个 step 只引入一件事，且必须能跑。
3. **不跳步**：先 loop，再 tools，再 prompt，再 session……后面的压缩 / 权限 / 记忆 / 多 Agent 按路线图继续。
4. **双后端**：Anthropic Messages API 与 OpenAI 兼容 API 都能用。

## 目录

```text
QianAgent/
├── README.md                 # 本文件
├── ROADMAP.md                # 完整搭建路线（读这个）
├── pyproject.toml
├── steps/                    # 每一步的「最小可运行切片」
│   ├── 01_agent_loop/
│   ├── 02_tools/
│   ├── 03_system_prompt/
│   └── 04_cli_session/
└── qian/                     # 当前累计版（把已完成步骤组装在一起）
    ├── __init__.py
    ├── __main__.py
    ├── agent.py
    ├── tools.py
    ├── prompt.py
    └── session.py
```

## 快速开始

```bash
cd QianAgent
pip install -e .

# OpenAI 兼容（例如你已有的中转）
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://.../v1"
export QIAN_MODEL="openai/gpt-oss-120b"

# 或 Anthropic
# export ANTHROPIC_API_KEY="sk-ant-..."

# 跑累计版
python -m qian "用一句话介绍你自己"

# 交互 REPL
python -m qian

# 权限 / 流式
python -m qian --yolo "列出 py 文件"
python -m qian --plan "分析 README 并给出改造计划"
python -m qian --dont-ask "创建 /tmp/x.txt"   # 会拒绝需确认的写操作
python -m qian --no-stream "你好"

# 只跑某一步的最小演示
python steps/01_agent_loop/run.py "你好"
python steps/02_tools/run.py "读取 README.md 前 5 行"
python steps/06_permissions/permissions.py   # 权限表自测，不调 API
```

## 当前进度

| 步骤 | 概念 | 状态 |
|------|------|------|
| 01 | Agent Loop：调模型 → 执行工具 → 回灌 → 重复 | ✅ |
| 02 | 工具系统：read / write / edit / shell | ✅ |
| 03 | System Prompt：行为约束与工具使用规范 | ✅ |
| 04 | CLI + 会话：REPL、保存/恢复 messages | ✅ |
| 05 | 流式输出：Anthropic / OpenAI stream | ✅ |
| 06 | 权限：default / yolo / plan / dont-ask | ✅ |
| 07+ | mtime、上下文压缩、记忆、Skills… | ⏳ 见 ROADMAP |

## 和 mini_claude / Mokio 的关系

| | QianAgent | mini_claude | MokioAgent |
|--|-----------|-------------|------------|
| 目标 | 自己的 agent，分步长出来 | Claude Code 教学复刻 | MultiAgent 工作流教学 |
| 编排 | 单 Agent tool-loop | 同左 | LangGraph 图 |
| 写法 | 逐步切片 + 累计包 | 完整成品 | 完整成品 |

本项目**不复制粘贴** mini_claude 源码，按同样思想重新实现。
