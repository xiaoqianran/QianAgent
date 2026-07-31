# Step 14 — MCP 集成

## 只解决一件事

用 stdio JSON-RPC 连外部 MCP server，把工具挂进主 loop。

配置示例 `.qian/settings.json`：

```json
{
  "mcpServers": {
    "demo": {
      "command": "node",
      "args": ["mcp-server.js"]
    }
  }
}
```

工具名：`mcp__demo__toolName`
