# QianAgent Roadmap

原则：**一个 Step 只引入一个核心概念，累计包始终可运行。**

## 心智模型

```text
Model = 决策器
Harness = 上下文 + 工具 + 权限 + 生命周期 + 状态 + 并发 + 恢复
Agent = Model 在 Harness 中反复执行，直到满足停止条件
```

## Phase 1 — 基础 Coding Agent（01–18）

| Step | 能力 | 状态 |
|---:|---|:---:|
| 01 | Agent Loop | ✅ |
| 02 | read/write/edit/shell/list tools | ✅ |
| 03 | System Prompt | ✅ |
| 04 | CLI / REPL / Session | ✅ |
| 05 | Streaming | ✅ |
| 06 | Permission modes | ✅ |
| 07 | read-before-write + mtime | ✅ |
| 08 | large tool-result persistence | ✅ |
| 09 | snip + compact | ✅ |
| 10 | project Memory | ✅ |
| 11 | Skills | ✅ |
| 12 | Plan mode | ✅ |
| 13 | fork-return Subagent | ✅ |
| 14 | MCP stdio | ✅ |
| 15 | budget + abort | ✅ |
| 16 | API usage / cost | ✅ |
| 17 | safe parallel tool calls | ✅ |
| 18 | MCP demo integration | ✅ |

## Phase 2 — Harness Engineering（19–27）

| Step | 目录 | 只解决什么 | 成功标准 |
|---:|---|---|---|
| 19 | `steps/19_hooks` | 生命周期 Hooks + trace | Prompt/Tool/Stop 可拦截、变换、记录 |
| 20 | `steps/20_task_system` | Todo + durable Task DAG | 依赖校验、claim、complete、自动解锁 |
| 21 | `steps/21_background_tasks` | 非阻塞长命令 | run/check/list/cancel + 完成通知 |
| 22 | `steps/22_cron_scheduler` | 持久定时自治 | 5-field cron、重启恢复、分钟去重 |
| 23 | `steps/23_agent_teams` | 多 Agent 协作 | mailbox、broadcast、自治领任务、plan review |
| 24 | `steps/24_workflow_runtime` | 可复用确定性编排 | pipeline/parallel、journal、resume |
| 25 | `steps/25_goal_loop` | 自主停止条件 | 未达目标阻止 Stop；达成/不可能/上限可退出 |
| 26 | `steps/26_worktree_isolation` | 并行修改隔离 | 独立 branch/worktree + 安全回收 |
| 27 | `steps/27_integrated_harness` | 统一接线 | 所有能力仍围绕唯一 Agent loop 工作 |

## 同步强化

Step 19–27 集成时，原有能力也被补强：

- Context：模型侧 `compact` + proactive compact + provider overflow reactive retry。
- Memory：自动 durable extraction + transactional consolidation + path traversal guard。
- Permission：工作区外文件访问不再静默放行。
- Subagent：剥离 coordinator tools，防止 Team/Cron/Workflow 递归扩张。
- Cron：持久任务在 Harness 启动时自动恢复服务；pending-delivery 提供失败后至少一次重试。
- Session：`--resume` 同时恢复 Todo、active Goal 与 usage/turn 状态。
- Recovery：非流式 provider transient error 有界退避；Context overflow 独立走 compact。
- Runtime state：`.qian` 写入增加 symlink/workspace 边界校验；shell 生命周期回收 process group。
- Workflow：input schema + step/parallel/time limits，resume 前重新校验冻结参数。

## 模块边界

```text
agent.py       唯一循环与模型调用
  │
  ├─ tools.py / permissions.py
  ├─ context.py / memory.py / skills.py
  ├─ subagent.py / mcp_client.py
  └─ harness.py
       ├─ hooks.py
       ├─ todo.py + tasks.py
       ├─ background.py + scheduler.py
       ├─ teams.py
       ├─ workflows.py
       ├─ goals.py
       └─ worktrees.py
```

**刻意不做：**把代码拆成深层 `core/graph/nodes/...`，或为了“看起来像框架”而把一个简单 loop 藏进复杂抽象。

## 下一阶段候选

Phase 3 不应继续无脑堆工具，优先围绕生产可靠性：

1. provider capability registry / structured outputs；
2. SQLite event store + crash recovery；
3. sandbox / container execution boundary；
4. workflow schema versioning；
5. eval harness（任务成功率、tool error rate、token/cost、latency）；
6. web/TUI observability，而不是改变 Agent 核心语义。
