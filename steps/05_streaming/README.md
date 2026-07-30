# Step 05 — 流式输出

## 只解决一件事

模型一边生成，一边在终端打字，而不是等整段回复结束。

```text
以前:  create() → 拿到完整 reply → print 全文
现在:  stream() → 每个 text delta 立刻 print → 收齐后再处理 tool_use
```

## 为什么现在才做

Loop / tools 先通，再加流式：流式不改变「消息怎么存、工具怎么回灌」，只改变「文本怎么显示」。

## 实现要点

| 后端 | 做法 |
|------|------|
| Anthropic | `messages.stream` → `text_stream` 打印 → `get_final_message()` 取完整 content |
| OpenAI | `stream=True` → 拼 content delta 与 tool_calls delta → 组装与非流式相同的 assistant message |

## 本步不做

流式中途提前执行只读工具（early tool start）——那是性能优化，后面再加。
