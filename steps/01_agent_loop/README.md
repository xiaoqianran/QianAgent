# Step 01 — Agent Loop

## 只解决一件事

**Agent 是什么？** 不是图，不是多节点。就是：

```text
messages = []
把用户话 append 进 messages
while True:
    reply = LLM(messages, tools=...)
    messages.append(assistant reply)
    if 没有 tool_use:
        结束本轮
    执行每个 tool → 把 tool_result 放回 messages
```

## 本步包含

- `messages` 列表（对话状态的唯一真相）
- `chat()` 循环
- 双后端客户端工厂（Anthropic / OpenAI 兼容）
- **还没有真实工具**：`tools=[]`，只能纯聊天

## 本步不包含

工具、权限、压缩、会话、REPL……

## 怎么跑

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export QIAN_MODEL=...
python steps/01_agent_loop/run.py "用一句话介绍你自己"
```

## 读完应能回答

1. 为什么 tool 调用必须回到 messages 里？
2. 什么时候跳出 while？
3. Anthropic 与 OpenAI 的 message 形状差在哪？（本步用统一薄封装）
