# Step 11 — Skills

## 只解决一件事

把可复用工作流写成 `SKILL.md`，模型或用户用 `/name` 调用。

```text
.qian/skills/<name>/SKILL.md
---
name: greet
description: ...
---
Prompt body with $ARGUMENTS
```

- 工具 `skill`
- REPL `/<skill-name> args`
