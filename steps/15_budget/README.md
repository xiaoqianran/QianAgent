# Step 15 — 预算与中断

## 只解决一件事

长任务可控：

| 能力 | 说明 |
|------|------|
| `--max-turns N` | 模型回合数上限 |
| `--max-cost USD` | 粗估费用上限（按 token 粗算） |
| Ctrl+C / abort | 中断当前循环 |

超限时：为未完成的 tool_use 回填拒绝结果，避免 messages 结构损坏。
