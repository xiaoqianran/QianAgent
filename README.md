# QianAgent

QianAgent 是一个从经典 **LLM tool-loop** 一步步长出来的轻量 Coding Agent（Python 3.11+）。

这次 `1.0.0` 把原有 Step 01–18 扩展到 **Step 01–27**：在保留简单主循环的前提下，参考 `learn-claude-code` 的 harness engineering 思路，补齐 Hooks、Todo/Task、后台任务、Cron、Agent Teams、工作流、Goal Loop、Git Worktree 隔离和统一 Runtime Harness；同时增强上下文压缩、自动持久记忆与工作区边界权限。

> 目标不是复制 Claude Code，而是把“模型的 agency”和“运行时 harness”拆开：模型负责决定下一步，QianAgent 负责提供安全、可恢复、可观测的执行环境。

## 核心模型

```text
用户请求
  ↓
UserPromptSubmit Hook
  ↓
messages + memory recall + runtime notifications
  ↓
┌──────────────── Agent Loop ────────────────┐
│ snip / auto compact / reactive compact     │
│                ↓                           │
│              LLM                           │
│          ┌─────┴─────┐                     │
│       no tools     tool calls              │
│          │             ↓                    │
│      Goal/Stop   PreToolUse Hook            │
│          │        → Permission              │
│          │        → Execute                 │
│          │        → PostToolUse Hook        │
│          │             ↓                    │
│          └──────── tool_result ──────────────┘
└────────────────────────────────────────────┘
  ↓
auto memory extraction + Session trace
```

## 1.0.0 新增能力

| Step | 能力 | 说明 |
|---:|---|---|
| 19 | Hooks + Trace | Session/User/Tool/Stop 生命周期拦截；JSONL trace |
| 20 | Todo + Task DAG | Session scratchpad + 持久依赖任务图、claim/complete/unblock |
| 21 | Background Tasks | 非阻塞 shell、状态查询、取消、完成通知 |
| 22 | Cron Scheduler | 5-field cron、持久化、进程重启恢复、pending-delivery 至少一次执行 |
| 23 | Agent Teams | teammate、mailbox、broadcast、自治领取 durable task、plan review 协议 |
| 24 | Workflow Runtime | `.qian/workflows/*.json`，input contract、pipeline/parallel、limits、journal、resume |
| 25 | Goal Loop | 可验证停止条件；未满足时自动阻止 Stop 并继续 |
| 26 | Worktree Isolation | 每任务独立 Git worktree/branch，支持 run/status/keep/remove |
| 27 | Runtime Harness | 把上述能力统一接入同一个 Agent loop，而不是堆第二套框架 |

已有 Step 01–18 的 Loop、文件工具、流式、权限、mtime、大结果落盘、Context、Memory、Skills、Plan、Subagent、MCP、预算/中断、usage 和安全并行均保留。

### 同步增强的旧能力

- `compact` 现在既可由 CLI `/compact` 调用，也可由模型主动调用。
- 上下文先 snip；超过 `QIAN_AUTO_COMPACT_CHARS` 后 proactive compact；若供应商返回 context overflow，再 reactive compact 一次后重试。
- Memory 每轮结束可自动提取稳定的跨会话事实；排除 secrets、credentials、临时任务状态、原始 tool output 与助手猜测；达到阈值后事务式合并去重。
- Memory 文件访问增加目录边界检查，拒绝 path traversal。
- `read_file/write_file/edit_file/list_files` 访问工作区外路径，在普通模式下必须显式确认；`dontAsk/plan` 拒绝。
- 子 Agent 不继承 Cron/Team/Workflow/Worktree/Background 等协调器工具，避免递归自治失控。
- `--resume` 现在同时恢复 messages、Todo、active Goal、turn/token/compact 统计；Task/Cron/Workflow/Worktree 仍从各自 durable store 恢复。
- 非流式模型请求遇到 429、overloaded、timeout、5xx 等瞬时错误会做有界指数退避；流式请求不自动重放，避免重复输出。
- `.qian` 持久状态写入统一经过 workspace/symlink 边界校验；前台与后台 shell 都回收原 process group，减少 detached child 泄漏。

## 快速开始

```bash
git clone https://github.com/xiaoqianran/QianAgent.git
cd QianAgent
pip install -e .
cp .env.example .env
```

OpenAI-compatible：

```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://your-gateway/v1
QIAN_MODEL=gpt-4o
```

Anthropic：

```bash
ANTHROPIC_API_KEY=sk-ant-...
QIAN_MODEL=claude-sonnet-4-6
```

运行：

```bash
python -m qian "检查这个项目并修复测试"
python -m qian --yolo "实现需求并运行测试"
python -m qian --plan "只分析并输出计划"
python -m qian --dont-ask "CI 环境下执行安全任务"
python -m qian --resume
```

常用运行时开关：

```bash
python -m qian --no-trace ...
python -m qian --no-auto-memory ...
python -m qian --auto-compact-chars 200000 ...
python -m qian --max-turns 30 --max-cost 1.0 ...
```

对应环境变量：

```bash
QIAN_TRACE=0                 # 关闭 trace
QIAN_AUTO_MEMORY=0           # 关闭自动 memory extraction
QIAN_AUTO_COMPACT_CHARS=0    # 关闭 proactive compact（reactive 仍保留）
QIAN_GOAL_BLOCK_CAP=8        # Goal 阻止 Stop 的最大次数
QIAN_MODEL_RETRIES=2         # 非流式 transient provider error 重试次数（0-5）
```

## REPL

```text
/clear       /turns       /cost         /context
/compact     /memory      /skills       /plan
/todo        /tasks       /background   /crons
/team        /workflows   /goal [condition|clear]  /worktrees
/trace       /<skill>     exit
```

## Runtime 工具

### 短期与持久任务

- `todo_write`：当前 session 的短期计划；最多一个 `in_progress`。
- `task_create/list/get/claim/complete/update`：持久任务 DAG，支持依赖、owner、自动解锁。

### 并发与自治

- `background_run/check/list/cancel`
- `schedule_cron/list_crons/cancel_cron`
- `team_spawn/send/broadcast/inbox/list/shutdown/plan_review`
- `goal_set/status/clear`；REPL 也支持 `/goal <condition>`、`/goal clear`。

高层自治工具在 `default` 模式会先确认；`--dont-ask` 会拒绝，`--yolo` 才直接放行。

### Workflow

工作流放在 `.qian/workflows/*.json`：

```json
{
  "name": "review",
  "description": "Inspect, test, then summarize",
  "input_schema": {
    "type": "object",
    "properties": {"target": {"type": "string"}},
    "required": ["target"],
    "additionalProperties": false
  },
  "limits": {"max_steps": 16, "max_parallel": 4, "timeout_seconds": 600},
  "steps": [
    {
      "id": "inspect",
      "type": "agent",
      "agent_type": "explore",
      "prompt": "Inspect {{args.target}} and report risks"
    },
    {
      "id": "checks",
      "type": "parallel",
      "steps": [
        {"id": "tests", "type": "shell", "command": "python -m unittest discover -v"},
        {"id": "review", "type": "agent", "prompt": "Review: {{steps.inspect.output}}"}
      ]
    }
  ]
}
```

运行结果持续写入 `.qian/runtime/<run_id>.json`，失败后可 `workflow_resume`。`input_schema` 支持 object/array/string/integer/number/boolean、`required`、`enum` 和 `additionalProperties=false`；静态 step 数、parallel 宽度和单次执行时间都有硬上限。

### Worktree

当两个修改任务真正需要并行且会改同一仓库时：

```text
worktree_create → 独立 .qian/worktrees/<name> + qian/<name> branch
worktree_run    → 在隔离目录运行命令
worktree_status → 查看状态
after review: worktree_keep / worktree_remove
```

普通单线修改不要使用 Worktree。

## 目录结构

```text
QianAgent/
├── qian/
│   ├── agent.py          # 唯一主 Agent loop
│   ├── harness.py        # Runtime composition root
│   ├── hooks.py          # lifecycle + trace
│   ├── todo.py           # session scratchpad
│   ├── tasks.py          # durable DAG
│   ├── background.py     # async subprocess
│   ├── scheduler.py      # durable cron
│   ├── teams.py          # teammate + mailbox/protocol
│   ├── workflows.py      # declarative workflow runtime
│   ├── goals.py          # stop-condition controller
│   ├── worktrees.py      # git isolation
│   ├── context.py        # persist/snip/compact
│   ├── memory.py         # recall/extract/consolidate
│   ├── permissions.py    # policy boundary
│   ├── tools.py          # tool schema + local execution
│   ├── subagent.py       # bounded fork-return workers
│   ├── mcp_client.py     # MCP stdio
│   └── ...
├── steps/01_* ... 27_*   # 一步一个概念
├── tests/test_runtime_extensions.py
├── examples/
└── docs/runtime-architecture.md
```

运行时产生的数据默认不入 Git：`.qian/tasks/`、`.qian/team/`、`.qian/runtime/`、`.qian/worktrees/`、`.qian/traces/`、`.qian/scheduled_tasks.json`。Skills 与 Workflows 可以作为项目配置入库。

## 自测

完整离线回归：

```bash
python -m compileall -q qian steps tests
PYTHONPATH=. python -m unittest -v tests.test_runtime_extensions
PYTHONPATH=. pytest -q tests/test_runtime_extensions.py

python steps/07_mtime/test_mtime.py
python steps/08_context_light/test_persist.py
python steps/09_context_heavy/test_snip.py
python steps/10_memory/test_memory.py
python steps/11_skills/test_skills.py
python steps/13_subagent/test_subagent.py
python steps/14_mcp/test_mcp_unit.py
python steps/16_usage/test_usage.py
python steps/17_parallel_tools/test_parallel.py
python steps/18_mcp_demo/test_mcp_demo.py
```

## 与 learn-claude-code 的关系

QianAgent 使用 `learn-claude-code` 作为**能力与 harness 设计参考**，不是源码镜像：

- 保留 QianAgent 原有双后端、MCP、permission modes、mtime 与分步教学结构。
- 把 `learn-claude-code` 当前课程中的 Hooks、Todo、Task、Background、Cron、Teams、Harness、Workflow、Goal 等概念按 QianAgent 模块边界重新实现。
- 额外把 legacy Worktree isolation 纳入累计运行时。
- 所有协调器能力最终都回到同一个 tool-loop；不引入 LangGraph，也不建立第二套隐藏 Agent 框架。

更详细的依赖边界和安全模型见 [`docs/runtime-architecture.md`](docs/runtime-architecture.md)。

## License

MIT。
