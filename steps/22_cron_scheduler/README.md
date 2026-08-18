# Step 22 — Durable Cron Scheduler

## 这一步只解决什么

使用本地时区 5-field cron 调度隔离 Agent turn；durable job 在进程重启后恢复。

## 关键语义

- 到期时先把 `pending_delivery=true` 落盘，再执行 callback。
- callback 失败时不确认、不删除 one-shot；下一分钟继续重试。
- callback 成功后才清 pending / 删除 one-shot，因此提供 **at-least-once** 而不是 silent loss。
- schedule/cancel 的 durable 写失败会回滚内存状态；ID 冲突会重新分配。
- `.qian/scheduled_tasks.json` 每次 IO 前验证 workspace/symlink 边界。

## 成功标准

离线测试覆盖持久化、重启恢复、ID collision、写盘失败回滚、one-shot 失败重试与 symlink escape。
