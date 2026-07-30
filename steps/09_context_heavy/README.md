# Step 09 — 上下文压缩（重）：snip + compact

## 只解决一件事

长对话时控制 messages 体积：

1. **snip**：总字符超预算 → 旧 tool_result 换成  
   `[Content snipped — re-read with read_file if needed]`  
   最近 N 条保留。
2. **compact**：`/compact` 调模型写摘要，替换整段历史（保留 system）。

## 调用时机

- snip：每次 `_call_model` 之前自动
- compact：用户在 REPL 输入 `/compact`，或以后自动阈值触发

## 本步不做

prompt cache 断点、向量记忆。
