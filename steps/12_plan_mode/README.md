# Step 12 — Plan Mode 完整工作流

## 只解决一件事

只读规划 → 写出计划文件 → 用户审批 → 再执行。

```text
enter_plan_mode
  → permission=plan（只读 + 可写 plan 文件）
  → 模型调研并写入 plan 文件
exit_plan_mode
  → 展示计划
  → 用户四选一:
      1 clear-and-execute
      2 execute（保留上下文）
      3 keep-planning（可带反馈）
      4 abort
```

CLI: `--plan` 启动即 plan；REPL `/plan` 切换。
