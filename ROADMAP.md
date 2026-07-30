# QianAgent 搭建路线图

每一行 = 一次「只加一件事」的提交级步骤。  
做完一步，必须能单独跑通，再进入下一步。

---

## Phase 0 — 心智模型（不写代码也能讲清）

```text
用户消息
  → 放进 messages
  → while True:
        调用 LLM（带 tools schema）
        若无 tool_use → 打印文本，结束本轮
        若有 tool_use → 本地执行 → tool_result 塞回 messages → 继续
```

这就是全部。后面所有功能都是挂在这个环上的插件。

---

## Phase 1 — 能干活的最小 Coding Agent

| 步 | 目录 | 只做什么 | 成功标准 |
|----|------|----------|----------|
| **01** | `steps/01_agent_loop` | 消息列表 + while 循环 + 假/真模型接口 | 无工具也能对话；有工具调用框架 |
| **02** | `steps/02_tools` | 4 个工具：read / write / edit / shell | 能读写当前目录文件、跑命令 |
| **03** | `steps/03_system_prompt` | 抽出 system prompt 构造 | 模型优先用专用工具而非乱 shell |
| **04** | `steps/04_cli_session` | CLI 参数 + REPL + session 落盘 | `python -m qian` 可多轮；`--resume` |
| **05** | `steps/05_streaming` | 流式打印文本 | 边生成边显示，messages 形状不变 |
| **06** | `steps/06_permissions` | default/yolo/plan/dontAsk | 危险 shell / 新文件可确认或拒绝 |
| **07** | `steps/07_mtime` | 读前再改 + mtime | 未 read 禁止 edit；外部修改强制重读 |
| **08** | `steps/08_context_light` | 大结果落盘 | >30KB 写 `~/.qian/tool-results/` |
| **09** | `steps/09_context_heavy` | snip + compact | 旧 tool_result 占位；`/compact` 摘要 |

当前累计包 `qian/` = 01～09 的合体。

---

## Phase 2 — 用起来像真 CLI（续）

| 步 | 概念 | 关键点 |
|----|------|--------|
| （07–09 已完成，见上表） | | |

---

## Phase 3 — 进阶能力

| 步 | 概念 | 关键点 |
|----|------|--------|
| **10** | 记忆 | 项目级 md 记忆 + 简单召回 |
| **11** | Skills | `.qian/skills/*/SKILL.md` |
| **12** | Plan mode | 只读规划 → 用户批准再执行 |
| **13** | 子 Agent | `agent` 工具 fork-return |
| **14** | MCP | stdio JSON-RPC 外挂工具 |
| **15** | 预算 / 中断 | max_turns、max_cost、Ctrl+C abort |

---

## 模块长成后的目标形态（扁平）

```text
qian/
  __main__.py      # CLI + REPL
  agent.py         # 循环 + 权限 + 压缩调度
  tools.py         # 工具 + mtime
  permissions.py   # 权限模式
  context.py       # 落盘 / snip / compact
  prompt.py        # system prompt
  session.py       # 会话持久化
  # 后面：memory.py / skills.py / subagent.py / mcp_client.py
```

**刻意不做**：深层 `qian/core/graph/nodes/...` 分包。一个概念一个文件。

---

## 实现纪律

1. 新能力先写 `steps/NN_xxx/README.md` 讲清「这一步只解决什么」。
2. 再写该步最小可运行代码。
3. 最后把变更合入 `qian/` 累计包。
4. 每步保留「能讲给别人听的注释」，不写魔法。
