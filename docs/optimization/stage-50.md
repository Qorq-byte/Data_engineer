# 阶段50：三处"看起来成功"的收口

2026-10-31。阶段49 结束时的盘点把未完成项分成四组，本阶段处理其中**立刻可做、无外部依赖**的那一组里的代码层问题：`tool_executor` 的伪成功、PostgreSQL 索引列恒空、反馈两条路径各说各话。连带修掉一处同源问题（编排层把"未执行"当成执行结果）。

四项都只改语义、不改能力边界：没有新增数据库支持，没有引入真实模型调用，也没有让任何未测的东西变得"已测"。证据见 [stage-50-consistency.json](results/stage-50-consistency.json)。

## 1. `tool_executor` 未配置 MCP 时不再返回成功

旧代码在 `MCPClient` 未配置时走这条分支：

```python
return self._ok(
    {"stub": True, "server": server, "tool": tool, "args": args},
    note="No MCP client configured — returning stub result",
)
```

`status=DONE` 且 `data` 是一个正常 dict，于是：

- 调用方无法从状态上区分"执行完了"和"根本没执行"；
- `collaboration.run_pipeline` 的上下文只要看到 `execution_result` 就往下传（`sub_ctx.get("execution_result")`）；
- `nodes/respond.py` 用 `execution is not None` 判断 `has_result`。

改成失败：

```python
return self._error(
    f"No MCP client configured; {server}.{tool} was not called",
    latency_ms=round(elapsed, 2),
    server=server,
    tool=tool,
)
```

`_error` 的 `data` 是 `None`，所以不会再被任何一层读成结果。

没有新增错误码字段：`AgentResult` 只有 `errors: list[str]`，加 `code` 会牵动全部 agent 的序列化与前端契约；需要区分"没配"和"执行失败"时读 `errors[0]` 文本即可。这一取舍写在这里，不假装它已是结构化错误。

测试从"断言返回 stub"改成三条：状态是 `ERROR`、消息包含 `db_server.list_tables was not called`、`result.data is None`。`tests/test_agents/test_tool_executor.py` 14 项通过。

## 2. PostgreSQL 索引列不再恒为空

旧实现查 `pg_indexes`，只拿 `indexdef` 文本：唯一性靠 `"UNIQUE" in indexdef.upper()` 猜，列干脆写死：

```python
columns=[],  # Parsing indexdef is complex; stub for now
```

新查询直接问 `pg_index`，用 `pg_get_indexdef(indexrelid, ord, true)` 逐列取值：

```sql
SELECT i.relname AS indexname,
       ix.indisunique AS is_unique,
       array_agg(pg_get_indexdef(ix.indexrelid, k.ord, true)
                 ORDER BY k.ord) AS columns
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
CROSS JOIN LATERAL generate_series(1, ix.indnkeyatts) AS k(ord)
WHERE n.nspname = 'public' AND t.relname = $1
GROUP BY i.relname, ix.indisunique
ORDER BY i.relname
```

边界说明：

- `generate_series(1, ix.indnkeyatts)` 只取**键列**，INCLUDE 列不算进 `columns`；`array_agg(... ORDER BY k.ord)` 保持定义顺序。
- 表达式索引（如 `lower(name)`）返回的是表达式文本，不是列名——这是 `pg_get_indexdef` 的行为，没有额外的 `pg_attribute` 解析。
- 只覆盖 `public` schema，与原来的 `WHERE schemaname='public'` 一致，没有扩大范围。

真实库断言从"索引存在"加强到列内容：

```python
user_idx = next(i for i in orders.indexes if i.name == "orders_user_idx")
assert user_idx.columns == ["user_id"]
assert user_idx.is_unique is False
```

**验证边界必须分开说**：本地只能跑 `tests/test_db`（140 通过 / 1 跳过，不走真实 PG）；这条 SQL 只有在 CI 的"Real database acceptance"（postgres:16 服务）里才真正执行。所以"索引列已修好"这句话的证据是那次工作流，不是本地绿。

## 3. 反馈两条路径不再各说各话

改动前：

| 路径 | 落库 | 学习管线 | 返回 |
|---|---|---|---|
| `POST /api/v1/feedback` | MySQL（失败静默）+ 进程内 list | 调用 collector | 恒 `status=recorded`，恒"已记录至学习库" |
| `FeedbackAgent.execute` | 只有进程内 list | 不调用 | 恒 `acknowledged: True`，docstring 仍写 "Phase 4: stub" |

于是同一个用户的同一条反馈，走 REST 会进库、走 agent 不会，而两者都答"已记录"。

改后：

- **agent 写同一个 store**：`store.save_feedback(self._store_row(record, context))`，键名与 `MySQLStore.save_feedback` 读的 9 个字段一致；没有 store 或写入抛错时降级到进程内，并把异常写进 metadata 的 `persist_error`。
- **返回说清落在哪**：`{"storage": "mysql" | "process_memory", "persisted": bool}`；agent 另带 `learning_pipeline=False`，明确它不跑学习管线（它没有 NL 输入与会话上下文，构造不出可用的学习样本）。
- **REST 同样报告实际情况**：响应新增 `storage` / `persisted` / `learning`，`message` 按 `storage × learning` 四种组合给出不同措辞，不再一律"已记录至学习库"。
- **主键不会撞**：`feedback_id` 原来是 `fb_agent_{turn_id}_{len(self._records)}`，进程内计数器重启后从 0 开始，撞上 `uk_feedback_id` 唯一键就会整条 INSERT 失败（原来被 `except` 吞掉，现在至少报 `persist_error`）。改为随机后缀 `fb_agent_{uuid4().hex[:16]}`。

刻意没改的：`status` 仍是 `"recorded"`。进程内的 `_feedback_store` 确实记了一条，`/feedback/stats`、`/feedback/recent` 在无 MySQL 时就从它读；把 status 改成失败会同时改掉前端 `res.status === 'recorded'` 的判断（工作台、候选编辑、逐候选评分三处）。改的是"话说到什么程度"，不是"接口通不通"。

测试：

- agent 4 项——无 store 时 `storage=process_memory` / `persisted=False`；有 store 时收到的行字段逐个核对（键名错了 MySQL 会存进空字符串，所以必须按键断言）；store 抛错时请求仍成功但 `persist_error` 非空且进程内留档；两个新实例写同一 `turn_id` 得到不同 `feedback_id`。
- API 4 项——无 store、正常入库、store 抛错、collector 抛错四种组合的 `storage` / `persisted` / `learning` 与措辞。
- 有效性证明：把源码改动 `git stash` 后重跑同一批测试，**8 项全部失败**；恢复后 22 项通过。

## 4. 连带：编排层不再把"未执行"放进 `execution_result`

同一类问题的另一处。`collaboration.run_pipeline` 在没有 SQL 可执行时返回 `_stub_result("tool_executor", "No SQL to execute")`——`status=DONE` 且 `data={"stub": True}`，而 `orchestrator._synthesize` 对任何 dict 都执行 `output["execution_result"] = r.data`。

改成：

- `_skip_result()` 返回 `{"skipped": True, "reason": ...}`（语义是"跳过"，不是"桩实现"）；
- `_synthesize` 分流：`skipped` 进 `output["tool_skipped"]`，只有真的执行结果才进 `execution_result`。

没有给 `AgentStatus` 加 `SKIPPED`：SPEC §4.11.3 的状态机只有 IDLE/BUSY/DONE/ERROR，加成员是契约变更，不该夹在语义修正里做。

新增 3 项测试：skip 不进 `execution_result`、真实执行仍进、`_skip_result` 的载荷形状。前两项在旧代码上失败（旧代码会把 skip dict 塞进 `execution_result`），第三项在旧代码上是 `ImportError`——两种失败都说明测试确实绑在新契约上。

## 5. 验证

| 项 | 结果 |
|---|---|
| `ruff check app/ tests/` | All checks passed |
| `mypy app/` | 0 项 / 191 文件（strict 未放松） |
| `tests/test_agents` | 194 通过 |
| `tests/test_db` | 140 通过 / 1 跳过（不含真实 PG） |
| 全量 `pytest -m 'not real_database'` | **4265 通过 / 3 失败 / 1 跳过 / 82 排除** |

3 项失败与阶段 49 未改动树上完全相同（`test_cli_refuses_hardlink_input_alias`、两项 `test_usage_audit` 并发用例，Windows 平台既有问题），不是本阶段引入。

## 6. 没有覆盖的地方

- **MySQL 反馈写入没对真实 MySQL 跑过**：本地无 MySQL，agent 与 API 的用例用的都是 fake store；CI 的"Real database acceptance"覆盖 `tests/real_db` 里的 PG/MySQL 用例，但不经过反馈接口。所以准确说法是"两条路径调用同一方法、参数形状一致"，不是"双路径已在真实 MySQL 上验证一致"。
- **学习管线仍只有 REST 一条**：agent 明确返回 `learning_pipeline=False`，没有把 collector 接过去。要接的话得先让 agent 拿到 NL 输入和会话上下文，属于功能扩展。
- **字段集没有统一**：collector 的 SQLite 表有 `diff_count`/`diff_summary`/`execution_success`，MySQL 的 `feedback` 表没有；agent 算出的 diff 只进 metadata 的 `has_diff`，不落任何库。两边"字段一致"无从谈起，只是"能被同一张表写入"。
- **REST 的 `feedback_id` 仍是 `fb_{n}_{秒级时间戳}`**：进程内 n 递增，跨重启 + 同一秒理论上会撞键。本次没改，因为改它要动前端展示的 id 形态。
- 7 种方言、真实模型容量、业务准确率等与前一阶段相同的缺口没有变化，见[发布检查单](release-checklist.md)。
