# QianAgent × Learn Claude Code：12 话 Agent Harness 结构化笔记

> 本文档依据用户提供的《从零实现 Claude Code》系列转录材料重新梳理，并结合 QianAgent 当前仓库结构做模块映射。它不是逐字稿：口语重复、明显 ASR 误识别与术语拼写已经统一；章节中的“课程结论”只保留材料能够支持的内容。

## 0. 阅读方式：这套课程到底在讲什么

整套材料的主线不是“写一个神奇 Prompt”，而是把一个能工作的 Coding Agent 拆成 **Model + Harness / Runtime** 两层：

```text
Model
  └─ 感知 / 推理 / 决策“下一步做什么”

Harness / Runtime
  ├─ Tools：文件、Shell、搜索、外部能力
  ├─ Knowledge：Skill、项目约定、领域知识
  ├─ Observation：工具结果、测试、错误、任务状态
  ├─ Action Interface：Tool Schema / Handler / Dispatch
  └─ Boundaries：权限、上下文、生命周期、隔离
```

最底层始终是同一个闭环：

```text
User / Environment
        ↓
     Messages
        ↓
       LLM
        ↓
 Tool Call / Final
        ↓
     Runtime
        ↓
Tool Result / State
        ↓
     Messages
        └────────→ LLM
```

后面的 Todo、Subagent、Skill、Compact、Task、Background、Team、Protocol、Autonomous、Worktree 都是在这个 Loop 周围补工程能力，而不是替换 Loop。

---

## 1. S01 — Agent Loop：把“人肉循环”程序化

### 问题

普通 LLM 只完成一次输入到输出。它可以告诉人“读文件、改代码、跑测试”，但不会自己真正触碰环境。如果每次读文件、运行命令、复制错误、再把结果贴回对话都由人完成，那么真正的循环控制器其实是人。

### 核心机制

Agent Loop 把这个过程变成程序控制：

```python
messages = [user_message]

while True:
    response = llm(messages, tools=tools)
    messages.append(response)

    if response.stop_reason != "tool_use":
        return response

    results = execute_tool_calls(response)
    messages.append(results)
```

模型只负责提出结构化 Tool Call；Runtime 执行工具，再把 Tool Result 追加回 `messages`。只要模型仍要求使用工具，循环就继续。

### 需要记住

- `messages` 是 Agent 当前工作的短期上下文。
- Tool Call 是**意图**，不是执行本身。
- Agent 的持续性首先来自 Loop，而不是来自复杂工作流图。

### QianAgent 对应

- `qian/agent.py`：核心 Agent Loop、模型调用、工具调用回灌。
- `qian/session.py`：会话级状态与恢复相关支撑。

---

## 2. S02 — Tool Use：从一个 Bash 演化成工具系统

### 问题

只有 Bash 理论上什么都能做，但工程上不够可靠：读长文件可能被终端输出截断，字符串编辑容易受转义影响，路径访问也缺乏明确边界。

### 核心机制：Schema + Handler + Dispatch

每个工具由两部分组成：

```text
Tool Schema
  └─ 告诉模型：工具叫什么、参数是什么、什么时候适用

Tool Handler
  └─ Runtime 中真正执行动作的函数
```

再用 Dispatch Map 把模型意图映射到 Handler：

```python
TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
}

handler = TOOL_HANDLERS[tool_call.name]
result = handler(**tool_call.input)
```

这意味着新增能力时通常不需要改 Agent Loop，只需要增加 Schema 与 Handler。

### 安全边界

课程材料特别强调文件工具应经过安全路径解析，确保 Agent 只在允许的工作目录内读写。这里的“沙箱”首先是 **Agent 与操作系统/目录权限之间的边界**。

### QianAgent 对应

- `qian/tools.py`：文件、Shell 与工具分发。
- `qian/permissions.py`：执行前权限判断。
- `qian/hooks.py`：执行生命周期前后扩展点。

---

## 3. S03 — TodoWrite：把计划变成显式状态

### 问题

长任务中，模型容易出现：

- 重复已经完成的步骤；
- 遗漏后续步骤；
- 对话越长，最初计划的约束越弱；
- 工具结果持续占用上下文，导致注意力偏移。

### 核心机制

引入带状态的 Todo Manager，让计划不再只是自然语言段落，而是可持续更新的结构化状态：

```text
Todo
├─ task: 分析项目
│  └─ status: completed
├─ task: 修改实现
│  └─ status: in_progress
└─ task: 运行测试
   └─ status: pending
```

Todo 本身也可以作为工具暴露给模型。课程材料还加入了 Reminder：如果模型连续多轮不维护 Todo，Runtime 主动提醒它重新检查计划。

### 本质

Todo 的价值不是“把答案列成清单”，而是把**执行进度外部化**，让模型在每轮决策时都能重新看到当前目标与剩余步骤。

### QianAgent 对应

- `qian/todo.py`：Session 级 Todo 状态。

---

## 4. S04 — Subagent：用独立上下文隔离探索噪声

### 问题

一个复杂问题可能需要读取很多文件、执行多轮搜索、遇到报错再重试。如果所有中间过程都进入主 Agent 的 `messages`，主上下文会快速膨胀。

### 核心机制

父 Agent 通过 `task` 类工具启动一个短生命周期 Subagent：

```text
Parent Agent
   │
   ├─ “调查测试框架并给结论”
   ↓
Subagent（独立 messages）
   ├─ read
   ├─ search
   ├─ bash
   ├─ retry
   └─ summarize
   ↓
Final Summary
   │
   └────────→ Parent Agent
```

父 Agent 只接收最终摘要，不保留 Subagent 内部几十轮的工具噪声。

### Subagent ≠ Agent Team

- **Subagent**：任务级、短生命周期、完成即销毁，重点是上下文隔离。
- **Teammate**：长期存在、有身份、有 Inbox、有持续 Agent Loop，重点是协作。

### QianAgent 对应

- `qian/subagent.py`：临时子 Agent。

---

## 5. S05 — Skill Loading：知识按需进入上下文

### 问题

如果 Git、测试、代码审查、PDF、部署等每个工作流都塞进 System Prompt，技能越多，固定上下文成本越高，而且多数技能在当前任务里根本用不到。

### 核心机制：Progressive Disclosure

System Prompt 只暴露 Skill 的**名称 + 简短描述**：

```text
git        — Git 操作与提交约定
review     — 代码审查流程
pdf        — PDF 阅读与摘要流程
```

只有模型判断当前任务需要某个技能时，才调用 `load_skill`，把完整 `SKILL.md`（以及必要的 references / scripts 信息）作为 Tool Result 注入上下文。

```text
Skill Catalog（小）
       ↓
LLM 判断需要 git
       ↓
load_skill("git")
       ↓
完整 Skill 内容（大）
```

### 需要记住

- Tool 解决“**能做什么**”。
- Skill 解决“**应该怎样做**”。
- 先暴露索引，再按需拉取正文，能显著减少无关 token 占用。

### QianAgent 对应

- `qian/skills.py`。
- `.qian/skills/<skill-name>/SKILL.md`。

---

## 6. S06 — Context Compact：让有限窗口继续工作

### 问题

Agent Loop 天然会不断累积：用户消息、模型回答、Tool Call、Tool Result、文件内容、错误日志。若不管理，上下文最终会触顶。

### 核心机制

课程材料把压缩理解为多层策略，而不是“直接删历史”：

```text
大 Tool Result
  └─ 持久化到外部 / 用引用或截断替代

旧历史
  └─ 通过摘要压缩为更短的可继续上下文

接近阈值
  └─ 自动触发 Compact

用户需要
  └─ 手动 /compact
```

压缩后的关键不是保留每个字，而是保留：目标、已经完成的工作、重要约束、未完成任务、关键文件/错误与下一步。

### 风险

Compact 会丢失细节，所以后续自主 Agent 章节还需要重新注入 Identity，避免长期 Agent 在压缩后忘记自己的角色。

### QianAgent 对应

- `qian/context.py`：当前窗口压缩。
- 当前 QianAgent 另外有 `qian/memory.py` 处理跨 Session 信息；这是仓库后续扩展，和本课的“当前 Context Compact”不是同一生命周期。

---

## 7. S07 — Task System：从扁平 Todo 到持久任务图

### 问题

Todo 能告诉 Agent“还有哪些事情没做”，却不能充分表达复杂任务的依赖关系：什么必须先完成、什么可以并行、什么当前被阻塞。

### 核心机制：Task DAG

Task System 把任务持久化到磁盘，并加入结构化字段：

```text
Task
├─ id
├─ status: pending / in_progress / completed
├─ owner
└─ blocked_by: [task_id, ...]
```

例如：

```text
A: 搭建项目
├─ B: 实现核心代码
└─ C: 补充配置

B + C 完成
└─ D: 集成测试
```

只有依赖满足的任务才进入可执行状态。这样 Runtime 才能判断哪些任务可以并行，而不是要求模型凭记忆维护复杂顺序。

### Todo ≠ Task System

- Todo：Session 内的短期执行清单。
- Task System：可持久化、带依赖、可调度、可认领的系统级任务图。

### QianAgent 对应

- `qian/tasks.py`。
- `.qian/tasks/`。

---

## 8. S08 — Background Tasks：把等待从主循环移走

### 问题

安装依赖、运行测试、构建镜像等命令可能需要很久。如果主 Agent Loop 同步等待，模型在这段时间什么也做不了。

### 核心机制

把长耗时的确定性命令交给后台线程/子进程：

```text
Main Agent Loop
  ├─ 继续读代码 / 写配置 / 规划下一步
  │
  └─ Background Manager
       └─ subprocess: pytest / build / install
              ↓
           result queue
              ↓
     在后续模型调用前注入通知
```

### Background ≠ Subagent

- Background Task 没有 LLM，不会思考，只异步执行确定性工作。
- Subagent 有自己的 LLM Loop，会自主搜索、判断与重试。

### QianAgent 对应

- `qian/background.py`。
- `qian/scheduler.py` 是当前仓库进一步扩展出的持久计划任务能力。

---

## 9. S09 — Agent Teams：多个长期 Agent 如何共存

### 问题

Subagent 适合一次性委派，但复杂协作需要长期角色，例如 Lead、Coder、Tester。每个成员要有独立上下文、身份与生命周期，还要能交换消息。

### 核心机制

课程材料使用 Team Manager + append-only JSONL Inbox / Message Bus：

```text
              JSONL Inbox / Message Bus
             ↗          ↑           ↖
        Lead Agent   Alice Agent   Bob Agent
        own loop     own loop      own loop
```

Team 配置维护成员 roster/status。`send()` 向目标 Inbox 追加一行消息；每个 Agent 在调用模型之前读取自己的 Inbox，并把新消息注入上下文。

### 为什么用 Inbox

通信状态被放到模型上下文之外，形成可检查的外部通信层。Agent 不需要共享同一个 `messages`，也能协作。

### QianAgent 对应

- `qian/teams.py`。
- `.qian/team/inbox/`。

---

## 10. S10 — Team Protocols：从“能发消息”到“可靠协商”

### 问题

有 Inbox 只解决了“能不能通信”，没有解决“高影响操作如何可靠协调”。例如 Lead 直接终止正在写文件的 Teammate，可能留下半成品状态。

### 核心机制：Request / Response Handshake

高影响操作使用结构化握手：

```text
Lead → Bob: shutdown_request(request_id)
Bob  → Lead: approve / reject

approved:
  Bob cleanup → shutdown complete

rejected:
  Bob → reason
```

同样思路也可以用于计划审批：提交计划 → 对方批准或拒绝 → 再执行。

### 本质

Message Bus 是“传输层”；Protocol 是“协作语义”。可靠多 Agent 不只是彼此能说话，还必须约定关键状态转换的请求、确认、拒绝与完成语义。

### QianAgent 对应

- `qian/teams.py` 中的团队通信与协议状态。

---

## 11. S11 — Autonomous Agents：从 Push Task 到 Pull Task

### 问题

如果每个任务都必须由 Lead 明确分配，Team 规模增大后，Lead 会成为调度瓶颈。任务板上即使有多个 Ready Task，空闲 Agent 也不会主动做。

### 核心机制：WORK / IDLE + Claim

长期 Teammate 在工作结束后进入 IDLE：

```text
WORK
 ↓ stop tool use / task done
IDLE
 ├─ poll inbox
 ├─ scan task board
 └─ find: pending + unblocked + no owner
          ↓
       claim_task
          ↓
         WORK
```

课程示例中，Idle Agent 周期性检查任务；发现未被阻塞、无人认领的 Pending Task 后，把 owner 设置为自己再执行。长时间空闲则退出，避免无限驻留。

### Compact 后的 Identity

因为上下文压缩可能抹掉角色细节，长期 Teammate 在新一轮工作前需要重新注入自己的 Identity。

### QianAgent 对应

- `qian/teams.py` + `qian/tasks.py`。

---

## 12. S12 — Worktree + Task Isolation：并行之前先隔离

### 问题

多个 Agent 如果共享同一个 Git working directory，即使做的是不同 Task，也可能同时修改相同文件、覆盖彼此内容，而且难以干净回滚。

### 核心机制

为 Task 绑定独立 Git Worktree / Branch / CWD：

```text
Task A
  └─ worktree A
      └─ branch A

Task B
  └─ worktree B
      └─ branch B
```

Agent 执行 Tool 时使用对应 Worktree 作为 `cwd`。这样每个任务在独立工作目录中产生 diff，完成后再 Review / Merge。

### Worktree ≠ Sandbox

- Sandbox：Agent 与操作系统之间的权限边界。
- Worktree：Agent 与 Agent 之间的代码工作区隔离。

它们可以同时存在，但解决的是不同问题。

### QianAgent 对应

- `qian/worktrees.py`。
- `.qian/worktrees/`。

---

## 13. 最终章：12 个机制如何合成一个 Runtime

课程最终把所有机制重新放回同一个 Agent Loop 周围：

```text
                       ┌─ Skill Loader
                       ├─ Todo / Task State
                       ├─ Context Compact
                       ├─ Inbox / Identity
                       │
User → Messages → LLM ─┼─ Tool Dispatch ──→ Handlers / Environment
                       │        │
                       │        ├─ Subagent
                       │        ├─ Background
                       │        ├─ Team / Protocol
                       │        └─ Worktree cwd
                       │
                       └─ Tool Result / Notification
                                  ↓
                               Messages
                                  └────────→ LLM
```

可以把它压缩成四句话：

1. **Agent Loop 是控制核心**：负责持续“模型 → 行动 → 观察 → 再决策”。
2. **Tool Dispatch 是系统关节**：模型的结构化意图从这里接入真实函数、进程和文件系统。
3. **外部状态让目标不只存在于上下文**：Todo、Task、Inbox、Worktree 等把关键状态移到 Runtime。
4. **并发与自治必须有边界**：后台任务、长期 Team、自动 Claim 只有配合依赖、协议与隔离才可控。

---

## 14. 最容易混淆的概念

| A | B | 核心区别 |
|---|---|---|
| Tool | Skill | Tool 是动作接口；Skill 是按需加载的工作方法/知识 |
| Todo | Task System | Todo 是短期扁平计划；Task 是持久、带依赖的任务图 |
| Background | Subagent | Background 无 LLM；Subagent 有独立 LLM Loop |
| Subagent | Teammate | Subagent 短命且用于隔离噪声；Teammate 长期存在并通信 |
| Context Compact | 持久状态 | Compact 管当前窗口；Task/Inbox 等把关键状态放到窗口之外 |
| Message Bus | Protocol | Bus 解决消息传输；Protocol 解决可靠协商与状态转换 |
| Sandbox | Worktree | Sandbox 管系统权限；Worktree 管 Agent 之间的工作目录隔离 |
| Push Task | Pull Task | Push 由 Lead 指派；Pull 由空闲 Agent 自主 Claim Ready Task |

---

## 15. 对 QianAgent 的工程映射

当前 QianAgent 已经不只是课程的最小教学实现，还把部分机制扩展成更完整的 Runtime。对应关系可快速记为：

| 课程概念 | QianAgent 模块 |
|---|---|
| Agent Loop | `qian/agent.py` |
| Tools / Dispatch | `qian/tools.py` |
| Permission | `qian/permissions.py` |
| Todo | `qian/todo.py` |
| Task DAG | `qian/tasks.py` |
| Subagent | `qian/subagent.py` |
| Skill Loading | `qian/skills.py` |
| Context Compact | `qian/context.py` |
| Background | `qian/background.py` |
| Team / Inbox / Protocol | `qian/teams.py` |
| Worktree Isolation | `qian/worktrees.py` |
| Runtime 组合根 | `qian/harness.py` |
| Hooks / Trace | `qian/hooks.py` |
| Scheduler | `qian/scheduler.py` |
| Cross-session Memory | `qian/memory.py` |
| Goal / Stop Gate | `qian/goals.py` |

这层映射属于 **QianAgent 当前仓库实现说明**，不是对课程原始转录的逐字复述。

---

## 16. 一条完整任务如何流过系统

以“重构后端、补测试，并允许多个 Agent 并行”为例：

```text
User Goal
  ↓
Agent Loop
  ↓
Todo：当前会话计划
  ↓
Task DAG：拆任务 + 建依赖
  ├─ Task A → Worktree A → Coder
  └─ Task B → Worktree B → Tester
                ↓
         Skill 按需加载
                ↓
     Subagent 做高噪声调查
                ↓
Background 跑测试 / 构建
                ↓
Team Inbox 汇报结果
                ↓
Protocol 协调关键状态
                ↓
Review / Merge
```

这时模型仍然只是在每一轮选择“下一步做什么”，复杂性主要由 Runtime 承担：任务状态、执行、异步、通信、权限与隔离。

---

## 附录 A：关于“Claude Code 源码泄露”材料

用户提供的另一份转录材料讨论了所谓 Claude Code 源码/Source Map 泄露、社区 fork 与 Python/Rust 重构版本。**本笔记只把它视作课程背景材料，不将其中的“泄露”“Star 数”等时效性或真实性陈述当作已经独立验证的事实。**

对 QianAgent 真正有长期价值的不是该事件本身，而是事件引出的学习方式：把大型产品实现拆成可读的 Harness 机制，再用更小的 Python Runtime 重新实现与验证这些机制。

---

## 附录 B：本笔记使用的用户材料

- 《从零实现 Claude Code（序）—— Overview》
- 《从零实现 Claude Code（第一话）—— Agent Loop》
- 《从零实现 Claude Code（第二话）—— Tool Use》
- 《从零实现 Claude Code（第三话）—— TodoWrite》
- 《从零实现 Claude Code（第四话）—— Subagent》
- 《从零实现 Claude Code（第五、六、七话）—— Skill Loading、Context Compact、Task System》
- 《从零实现 Claude Code（第八、九、十话）—— Background Tasks、Agent Teams、Team Protocols》
- 《从零实现 Claude Code（第十一、十二话）—— Autonomous Agents、Worktree + Task Isolation》
- 《从零实现 Claude Code（最终章）—— Claude Code》
- 《Claude Code 源码泄露……》背景转录材料（未独立核验）
