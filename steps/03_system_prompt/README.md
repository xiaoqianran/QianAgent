# Step 03 — System Prompt

## 只解决一件事

把硬编码的一句话 system prompt 换成**可维护的行为规范**。

模型的行为很大一部分不靠代码，靠 prompt：

- 优先用专用工具（read/edit），少用 shell 顶替
- 先读再改
- 输出简洁
- 不编造没读过的文件内容

## 本步仍不做

CLAUDE.md 注入、@include、skills 列表、动态环境块……那些是后面的事。
