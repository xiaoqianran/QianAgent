# Step 24 — Workflow Runtime

## 这一步只解决什么

用 JSON 描述 deterministic pipeline/parallel 编排；运行 journal 可查询、失败可 resume。

## 关键语义

- `input_schema` 在创建 run 之前验证，resume 时再次校验冻结 args。
- 支持 `required`、`enum`、`additionalProperties=false` 与基础 JSON 类型。
- `limits.max_steps`、`limits.max_parallel`、`limits.timeout_seconds` 限制工作流执行面。
- 每个 step 状态持续写入 `.qian/runtime/<run_id>.json`，已完成 step 在 resume 时跳过。
- parallel 只允许叶子 agent/shell，避免共享 journal 的嵌套并发歧义。

## 成功标准

输入不合法时不创建 run；失败 run 可恢复；并行宽度与超时不可绕过。
