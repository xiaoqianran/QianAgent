# Step 16 — API usage 精确计费

## 只解决一件事

费用与 token 优先来自 API `usage` 字段，而不是整段 messages 字符除 4。

```text
response.usage
  → input_tokens / output_tokens
  → cost_usd(model 费率表)
拿不到 usage → 回退字符估算，并标记 from_api=False
```

REPL `/cost` 会显示是否来自 API。
