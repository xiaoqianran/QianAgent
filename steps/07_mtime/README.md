# Step 07 — 读前再改 + mtime

## 只解决一件事

改文件前必须先读过；读完后若磁盘上的文件被外部改过，必须重读。

```text
read_file(path)  → 记录 abs_path → mtime
edit/write 已存在文件:
  若从未 read → Error: 请先 read_file
  若 mtime 变了 → Error: 文件已外部修改，请重读
  成功写入后 → 更新 mtime
```

## 为什么

防止模型在「想象中的旧内容」上瞎改，也避免覆盖用户在编辑器里的未同步修改。

## 本步不做

权限、压缩、沙箱根目录限制。
