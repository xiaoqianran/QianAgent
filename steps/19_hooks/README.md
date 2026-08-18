# Step 19 — Lifecycle Hooks + Trace

## 这一步只解决什么

在 UserPromptSubmit/PreToolUse/PostToolUse/Stop/SessionStart/SessionEnd 暴露稳定拦截点，并记录 JSONL trace。

## 成功标准

- 累计实现位于 `qian/` 对应模块，不建立第二套 Agent loop。
- 离线行为由 `tests/test_runtime_extensions.py` 覆盖。
- 与旧 Step 01–18 的测试一起回归，不能破坏已有语义。

## 设计约束

这一层只提供确定性 harness 原语；是否调用它仍由 lead model 决定。高风险或自治操作必须继续经过 `permissions.py`。
