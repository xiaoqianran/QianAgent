# Step 08 — 上下文压缩（轻）：大结果落盘

## 只解决一件事

单次 tool 返回太大时，**先完整写磁盘**，再把短预览塞进 messages。

```text
tool 结果 > 30KB
  → ~/.qian/tool-results/<ts>-<id>-<tool>.txt
  → messages 里只留预览 + 路径
  → 模型需要全文时 read_file 该路径
```

## 为什么先落盘再截断

若先截断再存，信息就丢了。顺序必须是：**persist → preview**。

## 本步不做

旧结果 snip、整段 compact 摘要（Step 09）。
