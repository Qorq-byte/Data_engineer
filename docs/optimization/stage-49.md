# 阶段49：Agent 术语/规则匹配修复与依赖快照

2026-10-31。本阶段收口两件此前挂着的事：阶段47 定性但未改的 `SchemaRetrievalAgent` 死路径，以及阶段48 暴露的"没有 Python 锁文件、告警口径不稳"。证据见 [stage-49-dependencies.json](results/stage-49-dependencies.json)。

两次改动分开提交，互不混合：Agent 修复 `565f687`，依赖快照单独提交。

## 1. 术语与规则匹配一直是空的

`app/agents/schema_retriever.py` 里对两个**同步**方法用了 `await`，还传了一个它们不接受的参数名：

```python
terms = await self._glossary.match(sqr.raw_text, domain_id=domain_id)
matched_rules = await self._rules.match(sqr.raw_text, domain_id=domain_id)
```

`GlossaryManager.match(query_text, domain=None, threshold=...)` 和 `RuleEngine.match(query_text, domain=None, threshold=...)` 都返回 list，参数名是 `domain`。所以每次调用必然抛 `TypeError`（`await` 非 awaitable + 未知关键字），而两处都包在 `contextlib.suppress(Exception)` 里——异常被吞掉，`terms` 和 `rules` 恒为空列表。

阶段47 在清理类型债务时定位到它，但因为"修复会改变 Agent 输出"而保留。现在改：

- 同步调用，用 `domain=domain_id or None`。
- 不再用 `suppress`：匹配失败写入 metadata 的 `term_match_error` / `rule_match_error`。检索仍然成功，但失败可见，不会再出现"看起来跑通了、其实一直空"的情况。
- 顺带修正 docstring 里与实际类型不符的说明（`terms` 是 `(term, GlossaryTerm, score)` 三元组，`rules` 是 `RuleMatch`）。

**新增 5 项测试**，并且验证了它们确实能抓住这个缺陷：把源码改动 `git stash` 后重跑同一份测试，5 项全部失败（`10 passed, 5 failed`）；恢复后 `15 passed`。

其中 `test_domain_id_is_actually_forwarded` 是关键的：域内术语注册在 `ecommerce`，用 `domain_id="finance"` 查必须为空，用 `ecommerce` 查必须命中。只有 `domain` 真的被传下去才会这样——它不是"碰巧匹配到全局条目"。

### 影响面

- 下游只有 `agents/orchestrator.py` 和 `agents/collaboration.py` 把 `terms`/`rules` 透传进 output/context，没有结构假设，也不会进 LLM 提示词（提示词里同名的 `caps["rules"]` 是字符预算，不是这个列表）。
- 因此修复不改变 SQL 生成行为，只让 Agent 的输出契约成立。
- 全量本地 `pytest -m 'not real_database'`：**4254 passed / 3 failed / 1 skipped / 82 deselected**。3 项失败在未改动的 `.stage48` 树上同样失败（hardlink 与并发审计用例，Windows 平台既有问题）。

## 2. 依赖快照

`pyproject.toml` 只写下界，所以同一份源码在不同时间安装会得到不同运行时。阶段48 已经量到这个漂移：阶段44 的 30 题批次跑在 pydantic 2.14.1 / litellm 1.106.4 上，干净环境装出来的是 2.14.2 / 1.106.6。

新增 `constraints.txt`，锁**直接依赖**（25 个运行时 + 7 个 dev 工具），不锁传递依赖。理由与更新规则写在[依赖快照与升级口径](dependency-lock.md)。

验证分两步：

| 验证 | 命令 | 结果 |
|---|---|---|
| 解析 | `uv pip install -e '.[dev]' -c constraints.txt --dry-run` | 138 包解析成功，32 条约束全部采纳，**0 处不一致、0 处缺失** |
| 真装 | 同一命令在阶段48 干净环境执行 | 成功，124 → 138 包，运行时版本一个没变 |

只做 dry-run 是不够的，所以补了真装；但完整重装一个新 venv 卡在下载 lancedb（81.6 MB）超过 22 分钟，最后停在已装运行时的环境上加装 dev 部分。这一点如实记录：**"从零按约束装完整环境"没有被完整跑通**，跑通的是"解析全部约束 + 在已有运行时上补齐 dev"。

## 3. 依赖告警：13 项，全部在 filelock，且被上游阻塞

`pip-audit` 对 138 个包扫描，只有 1 个包报问题：

| 项 | 值 |
|---|---|
| 包 | `filelock` 3.32.7（传递依赖） |
| 告警数 | 13 |
| 修复版本 | 4.0.9 / 4.2.1 |
| 类型 | 锁路径在本地文件系统上的处理：符号链接、`mode` 被忽略、标记文件、SQLite 支撑的读写锁 |

**装不上修复版本**：

- `litellm` 硬性要求 `filelock>=3.16.1,<4.0`；
- 已发布的**最新** litellm 就是 1.106.6，PyPI 元数据里仍是 `filelock<4.0,>=3.16.1`；
- filelock 没有把修复回移到 3.x 的版本。

所以要修就得放弃或降级 litellm，代价明显大于收益。实际暴露面：

- 项目代码里**从不直接 import filelock**，只经 litellm 间接使用（`huggingface_hub` 的要求是 `>=3.10`，不是阻塞方）。
- 这些告警的前提都是本地攻击者能在锁目录预置文件或符号链接。当前是单机私有部署，多用户主机上才需要认真对待。

处理方式：**不忽略、不屏蔽**，把它作为已知风险记录，并写进发布检查单的缺口表。

这一条也顺带说明了为什么不能沿用历史数字：阶段21 记录的前端 7 项告警，在锁文件未变的情况下于阶段48 重跑得到 0 项——告警数量会随上游数据变化，只能记录"哪天、对哪份锁文件、实测多少"。

## 4. 依赖升级：试了一项，被 API 变更挡住，已回退

`uv pip list --outdated` 列出 9 个可升级包。逐项判断后只试 `sqlglot`（`30.18.0 → 30.27.0`）：它是直接依赖、本来就精确 pin、且与最新版差距最大。

**升级失败并被回退**：

| 项 | 结果 |
|---|---|
| 安装 | 30.27.0 装成功 |
| 全量测试 | **350 failed / 3907 passed**（升级前是 4254 passed / 3 failed） |
| 根因 | `sqlglot` 30.27.0 不再接受 `qualify(infer_schema=...)`；`app/core/schema_validation.py:56` 传了 `infer_schema=False` 和 `on_qualify=check_source`，于是所有走 schema 校验的路径抛 `TypeError: qualify() got an unexpected keyword argument 'infer_schema'` |
| 回退 | 重装 30.18.0，`tests/test_core` 从 `383 passed / 330 failed` 恢复为 **713 passed** |

结论：保留 30.18.0。适配新的 `qualify()` 签名是**代码改动**，需要单独验证列名限定与命名空间校验的行为是否等价，不能算作依赖维护。这也解释了为什么 `sqlglot` 从一开始就精确 pin——它不是一个可以随手跟新的库。

其余不升的理由写进证据 JSON：`openai` 与 `huggingface-hub` 是传递依赖且跨大版本（必须由上游放开）、`python-dateutil` 3.0.0 是主版本跳跃且没有告警驱动、`filelock` 被上游阻塞、`mypy`/`ruff` 是 dev pin 的补丁版本且需连带改 pyproject 并重跑全量类型检查。

## 5. 未做

- 完整从零按 `constraints.txt` 安装一个新环境（卡在下载，未跑完）。
- 把 `constraints.txt` 接进 CI 的 `pip install`（CI 是 Linux，快照来自 Windows，强套会把平台差异变成构建失败）。
- 重新在 pydantic 2.14.2 / litellm 1.106.6 上跑阶段44 的 30 题批次。快照解决了"装到什么版本"，但没有解决"这个版本组合是否被真实评测覆盖"。
