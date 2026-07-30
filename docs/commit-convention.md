# QianAgent Git 提交规范（阿里风格）

对齐常见阿里 / Angular 约定式提交，**一步一提交**。

## 格式

```text
<type>(<scope>): <subject>

<body>（可选）
```

### type

| type | 含义 |
|------|------|
| `feat` | 新功能（新 Step、新能力） |
| `fix` | 修 bug |
| `docs` | 文档 / 路线图 / README |
| `refactor` | 重构，不改行为 |
| `test` | 测试 |
| `chore` | 工程杂项（脚手架、依赖、ignore） |
| `perf` | 性能 |

### scope

- 步骤切片：`step-01` … `step-15`
- 累计包：`qian`
- 工程：`repo` / `build`

### subject

- 用**中文**简明动宾句，不超过 ~50 字
- 不加句号
- 例：`feat(step-07): 读前再改与 mtime 校验`

### body（推荐写清）

- 这一步**只**解决什么
- 怎么验证（命令）
- 若为补提交，注明「补记」

## 一步一提交（强制）

每完成 `ROADMAP` 中的一个 Step：

1. 更新 `steps/NN_*/` 切片（README + 最小代码）
2. 合入 `qian/` 累计包
3. 更新 `README.md` / `ROADMAP.md` 进度
4. **立刻** `git add` + `git commit`（不要攒多个 Step）

推荐命令：

```bash
git add steps/07_mtime qian README.md ROADMAP.md
git commit -m "$(cat <<'EOF'
feat(step-07): 读前再改与 mtime 校验

- edit/write 前必须先 read
- 外部修改后要求重读
- 验证: python -m qian --yolo '...'

EOF
)"
```

## 历史说明

仓库初始化时，Step 01–06 曾在同一会话内实现后补提交。  
补交历史见早期 commit message 中的「补记」字样。此后严格执行一步一提交。
