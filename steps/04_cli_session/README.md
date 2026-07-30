# Step 04 — CLI 与会话

## 只解决一件事

1. **CLI 入口**：`python -m qian [prompt]` 或交互 REPL  
2. **会话落盘**：把 messages 存成 JSON，支持 `--resume`

## 会话存哪

```text
~/.qian/sessions/<session_id>.json
```

只存 messages + 元数据。不做 checkpoint git、不做 trace 旁路——那些是更后面的 harness。

## REPL 最小命令

- 空行忽略
- `exit` / `quit` 退出
- `/clear` 清空历史
- `/cost` 占位（显示 turn 数；真费用统计以后加）
