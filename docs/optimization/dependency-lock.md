# Python 依赖快照与升级口径

2026-10-31。阶段48 在干净环境暴露了一个可复现性缺口：`pyproject.toml` 只写了下界，同一份源码在不同时间安装会得到不同运行时。

| 包 | 阶段44 评测清单冻结 | 阶段48 干净环境实测 |
|---|---|---|
| pydantic | 2.14.1 | 2.14.2 |
| litellm | 1.106.4 | 1.106.6 |
| Python | 3.12.13 | 3.12.13 |

阶段44 的 26/30 是在左列组合上跑出来的，右列没有重跑。本阶段补一份显式快照，让"用哪套版本跑过验收"变成可复制的文件，而不是靠时间点回忆。

## 做法

新增仓库根 `constraints.txt`，用 pip constraints 机制约束**直接依赖**：

```bash
python -m pip install -e . -c constraints.txt
python -m pip install -e '.[dev]' -c constraints.txt
```

只锁直接依赖（`pyproject.toml` 里声明的 25 个运行时包 + 7 个 dev 工具），**不锁传递依赖**。理由：

- 传递依赖的解析结果带平台指纹——Windows 会多装 `tzdata`、`pywin32` 之类，硬编码进仓库会让 Linux/CI 装上不该有的包或触发版本冲突。
- 真正造成漂移的是直接依赖（pydantic、litellm、fastapi 这些），锁住它们就解决了阶段48 记录的问题。
- 文件小到可以逐行审阅，改哪一行是显式的。

代价要说清楚：传递依赖仍然浮动。同一份 `constraints.txt` 在两个时间点安装，仍可能得到不同的 `pyarrow`/`grpcio`。这不是完整锁定，只是把最大的漂移源钉住。

## 已验证组合

`constraints.txt` 记录的是 CPython 3.12.13 / Windows 11 上于 2026-10-31 解析出的版本，即阶段48 干净环境部署与阶段49 验证所用的同一套。几个值得注意的点：

- `duckdb==2.0.0`：下界写的是 `>=1.1.0`，实际解析到 2.x 主版本。SQLite/DuckDB 是逐轮真实执行验收的两种库，主版本跳动会改变方言行为，因此这个版本必须显式记录。
- `sqlalchemy==2.1.6`、`bcrypt==5.0.0`、`pydantic-settings==2.16.2`：同样高于下界一个大版本段，均未单独压测。
- `sqlglot==30.18.0`：`pyproject.toml` 里本来就精确锁定，因为 SQL 解析行为敏感，与本文件重复声明是为了让快照自洽。

## 更新规则

1. 先改 `pyproject.toml` 的下界，再改 `constraints.txt` 的具体版本，两次改动分开提交。
2. 每次改动都要重跑：全新 venv 安装、`npm run build:frontend`、`pytest -m 'not real_database'`、CI 双工作流。改动了 LLM 或数据库驱动的，还要重跑真实双库验收。
3. 只有在一套新组合上重新跑过相关验收，才能在文档里写"已在新组合上验证"。阶段44 的 30 题结论**不会**自动迁移到新组合上。
4. 不把 `constraints.txt` 接进 CI 的 `pip install`：CI 跑 Linux，这份快照来自 Windows，强行套用会把平台差异变成构建失败。CI 仍按 `pyproject.toml` 解析，快照用于部署复现与人工核对。

## 依赖告警口径

阶段21 记录过前端构建链的 7 项告警（5 high / 2 moderate）。阶段48 重新核验时同一份 `package-lock.json` 得到 0 项，而锁文件在两个时点之间没有任何改动——差异只能来自 npm 注册表侧的 advisory 数据。因此：

- **不以告警数量作为"已修复"或"已恶化"的证据**，只记录"在哪一天、对哪份锁文件、跑出什么数字"。
- 前端构建依赖只在构建期执行，不进浏览器包、不被 Python 服务执行；但构建不可信提交仍有构建期拒绝服务风险，继续在隔离且资源受限的可信环境构建。

Python 侧的告警核验结果见[阶段49报告](stage-49.md)，那里记录的是对 `constraints.txt` 组合实测的数字，不引用任何历史结论。
