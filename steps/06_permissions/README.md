# Step 06 — 权限模式

## 只解决一件事

工具执行前先问一句：**允许吗？**

```text
模型要调 tool
  → check_permission(name, input, mode)
  → allow   → 执行
  → deny    → 把拒绝原因当 tool_result 回给模型
  → confirm → 问用户 y/n（可缓存同类确认）
```

## 模式

| 模式 | CLI | 行为 |
|------|-----|------|
| `default` | （默认） | 读放行；危险 shell / 写新文件要确认 |
| `bypass` | `--yolo` | 全放行 |
| `dontAsk` | `--dont-ask` | 该确认的直接 deny（适合 CI） |
| `plan` | `--plan` | 只读；禁 write/edit/shell |

## 危险 shell

用简单正则扫：`rm -rf`、`git push --force`、`mkfs` 等。  
不追求完美，只挡住最常见的高危模式。

## 本步不做

settings.json 规则引擎、Auto Mode 分类器、mtime 读前再改（Step 07）。
