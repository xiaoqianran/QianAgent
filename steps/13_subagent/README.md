# Step 13 — 子 Agent（fork-return）

## 只解决一件事

主 Agent 通过 `agent` 工具拉起**隔离上下文**的子 Agent，只收回文本结果。

```text
主 Agent
  → agent(type=explore|plan|general, prompt=...)
  → 新 Agent(is_sub_agent, 裁剪 tools, 独立 messages)
  → 跑完返回 text
  → 主 messages 只多一条 tool_result
```

不污染主会话的工具调用噪音。
