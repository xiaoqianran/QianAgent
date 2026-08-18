# Step 27 — Integrated Runtime Harness

## 这一步只解决什么

用一个 composition root 接线所有运行时能力，同时保持 `agent.py` 中唯一的 tool-loop。

## 集成保证

- lifecycle hooks、permission、tools、runtime notifications 都回到同一 messages/tool-result 循环。
- 子 Agent 有独立预算、usage 统计、context protection，不继承 coordinator tools。
- `--resume` 恢复 session-scoped Todo/Goal/usage；durable runtime 从各自 store 恢复。
- transient provider error 与 context overflow 分开恢复：前者退避重试，后者 compact。
- `.qian` state path、Memory path、foreground/background process group 都有边界/生命周期保护。

## 成功标准

新 runtime 回归全部离线通过，同时 Step 07–18 的原测试与真实本地 MCP stdio demo 全部保持通过。
