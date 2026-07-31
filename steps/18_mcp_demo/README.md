# Step 18 — MCP Demo Server

## 只解决一件事

给一个**可本地启动、零依赖**的 MCP stdio demo，验证 QianAgent 能发现并调用外挂工具。

```bash
# 终端 A：不必手动起；Agent 会按 settings 拉起
# 配置见 examples/mcp-settings.json

python -m qian --yolo "调用 mcp__demo__echo 传入 text=hello"
```

Demo 工具：
- `echo`：原样返回 text
- `add`：两数相加
