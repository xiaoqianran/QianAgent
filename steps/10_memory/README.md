# Step 10 — 记忆系统

## 只解决一件事

把「跨会话该记住的事」落到项目级文件，而不是只靠 messages。

```text
~/.qian/projects/<cwd-hash>/memory/
  MEMORY.md
  project_xxx.md
```

工具：
- `memory_save` / `memory_list` / `memory_get`

每轮用户消息前：`keyword_recall` 把相关记忆注入 prompt 旁路提醒。

## 本步不做

向量库、异步 LLM 语义选择器（以后可加）。
