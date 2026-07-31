# QianAgent

从零手写的 **Coding Agent**（Python）。

架构思路对齐 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 的 `mini_claude`，但**完全独立重写**，按最小步骤一层层搭起来，方便读懂、方便改。

- **仓库**：https://github.com/xiaoqianran/QianAgent  
- **当前版本**：`0.6.0`（Step **01–18** 已完成）  
- **运行时**：Python ≥ 3.11 · OpenAI 兼容 API / Anthropic Messages API  

---

## 它是什么

一条经典 **tool-loop**：

```text
用户消息 → 写入 messages
while True:
    调 LLM（带 tools schema）
    若无 tool_use → 输出文本，结束本轮
    若有 tool_use → 本地执行 → tool_result 回灌 → 继续
```

在此之上逐步挂上：权限、mtime、上下文压缩、记忆、Skills、Plan、子 Agent、MCP、预算、并行读工具等。

---

## 设计原则

1. **架构一眼能看懂**：扁平模块，不搞深层分包。  
2. **一步一个概念**：`steps/NN_*` 只引入一件事，且必须能跑。  
3. **不跳步**：先 loop，再 tools，再 prompt……按 [ROADMAP.md](ROADMAP.md) 推进。  
4. **双后端**：Anthropic 与 OpenAI 兼容中转都能用。  
5. **一步一提交**：阿里风格约定式 commit，见 [docs/commit-convention.md](docs/commit-convention.md)。  

---

## 技术栈（准确）

| 类别 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| LLM SDK | `openai`、`anthropic` |
| 配置 | `python-dotenv`（本地 `.env`，**不入库**） |
| CLI | `argparse` + 可选 Rich 依赖 |
| 架构 | 单 Agent tool-loop（**非** LangGraph） |
| 扩展 | Skills / 子 Agent / MCP stdio |
| 包管理 | `pip install -e .`（setuptools） |

**不是** Node.js / TypeScript 项目。累计运行时全在 `qian/*.py`。

---

## 目录结构

```text
QianAgent/
├── README.md / ROADMAP.md
├── pyproject.toml
├── docs/commit-convention.md
├── examples/
│   ├── mcp_demo_server.py      # Step 18 MCP demo
│   └── mcp-settings.json
├── .qian/skills/greet/         # 示例 skill
├── steps/                      # 每步最小可运行切片 + 自测
│   ├── 01_agent_loop/ … 18_mcp_demo/
└── qian/                       # 累计可运行包
    ├── __main__.py             # CLI / REPL
    ├── agent.py                # 主循环
    ├── tools.py                # 工具 + mtime + 并行安全集
    ├── permissions.py
    ├── context.py              # 落盘 / snip / compact
    ├── memory.py / skills.py
    ├── subagent.py / mcp_client.py
    ├── usage.py                # token / 费用
    ├── prompt.py / session.py
```

---

## 快速开始

```bash
git clone https://github.com/xiaoqianran/QianAgent.git
cd QianAgent
pip install -e .

# 复制并填写密钥（切勿提交 .env）
cp .env.example .env
```

### 配置 `.env`

**OpenAI 兼容中转：**

```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://your-gateway/v1
QIAN_MODEL=openai/gpt-oss-120b   # 或 gpt-4o 等
```

**Anthropic：**

```bash
ANTHROPIC_API_KEY=sk-ant-...
# 可选 ANTHROPIC_BASE_URL=...
QIAN_MODEL=claude-sonnet-4-6
```

### 运行

```bash
# 一次性任务
python -m qian "用一句话介绍你自己"
python -m qian --yolo "在当前目录写一个 hello.html 自我介绍页"

# 交互 REPL
python -m qian

# 常用开关
python -m qian --yolo "..."          # 跳过确认
python -m qian --plan "..."          # 只读规划
python -m qian --dont-ask "..."      # CI：需确认则拒绝
python -m qian --no-stream "..."
python -m qian --max-turns 20 "..."
python -m qian --max-cost 0.5 "..."
python -m qian --resume              # 恢复最近会话
```

### REPL 命令

| 命令 | 作用 |
|------|------|
| `/clear` | 清空对话 |
| `/turns` `/cost` `/context` | 回合 / 费用 / 上下文统计 |
| `/mode` `/plan` | 权限与 Plan 切换 |
| `/compact` | 摘要压缩历史 |
| `/memory` `/skills` | 列记忆 / 技能 |
| `/<skill>` | 调用 skill（如 `/greet 小明`） |
| `exit` | 退出 |

### MCP Demo（可选）

```bash
mkdir -p .qian
cp examples/mcp-settings.json .qian/settings.json
# 按需把 args 改成 examples/mcp_demo_server.py 的绝对路径
python -m qian --yolo "调用 mcp__demo__echo，text=hello"
```

---

## 能力一览（Step 01–18）

| 步骤 | 概念 | 状态 |
|------|------|------|
| 01 | Agent Loop | ✅ |
| 02 | 工具 read/write/edit/shell/list | ✅ |
| 03 | System Prompt | ✅ |
| 04 | CLI + 会话落盘 `~/.qian/sessions/` | ✅ |
| 05 | 流式输出 | ✅ |
| 06 | 权限 default / yolo / plan / dont-ask | ✅ |
| 07 | 读前再改 + mtime | ✅ |
| 08 | 大结果落盘 `~/.qian/tool-results/` | ✅ |
| 09 | snip + `/compact` | ✅ |
| 10 | 项目级文件记忆 + 关键词召回 | ✅ |
| 11 | Skills（`.qian/skills/*/SKILL.md`） | ✅ |
| 12 | Plan mode 审批流 | ✅ |
| 13 | 子 Agent（`agent` explore/plan/general） | ✅ |
| 14 | MCP stdio 客户端 | ✅ |
| 15 | 预算 + Ctrl+C 中断 | ✅ |
| 16 | API usage 精确计费 | ✅ |
| 17 | 只读工具并行 | ✅ |
| 18 | MCP demo server 联调 | ✅ |

更细的「每步只解决什么」见 [ROADMAP.md](ROADMAP.md)。

---

## 自测（无需 API Key 的部分）

```bash
python steps/07_mtime/test_mtime.py
python steps/08_context_light/test_persist.py
python steps/09_context_heavy/test_snip.py
python steps/10_memory/test_memory.py
python steps/11_skills/test_skills.py
python steps/13_subagent/test_subagent.py
python steps/14_mcp/test_mcp_unit.py
python steps/16_usage/test_usage.py
python steps/17_parallel_tools/test_parallel.py
python steps/18_mcp_demo/test_mcp_demo.py   # 会拉起本地 demo MCP
```

---

## 和 mini_claude / Mokio 的关系

| | QianAgent | mini_claude | MokioAgent |
|--|-----------|-------------|------------|
| 目标 | 自己的 agent，分步长出来 | Claude Code 教学复刻 | MultiAgent 工作流教学 |
| 编排 | 单 Agent tool-loop | 同左 | LangGraph 图 |
| 语言 | Python | TS + Python | Python |
| 写法 | 逐步切片 + 累计包 | 完整成品 | 完整成品 |

本项目**不复制粘贴** mini_claude 源码，按同样思想重新实现。

---

## 许可

MIT（见仓库 License；若未单独声明，以 GitHub 仓库设置为准）。
