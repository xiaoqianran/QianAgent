# Step 17 — 只读工具并行

## 只解决一件事

同一轮模型若一次发出多个**无副作用**工具调用，用线程池并行执行。

```text
CONCURRENCY_SAFE = {read_file, list_files, memory_list, memory_get, ...}
同一 turn 全是 safe → ThreadPoolExecutor 并行
有写/shell/agent → 保持串行（避免竞态）
```

不在本步做「流式 content_block 未完成就启动」的极致早启（可后续加）。
