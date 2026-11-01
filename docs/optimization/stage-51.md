# 阶段51：把每请求的全量 Schema 处理从热路径上拿掉

2026-10-31 起，2026-11-01 完成。阶段45 只测量、不改码，留下三个已证实的瓶颈：单连接 lease 串行、同步工作阻塞事件循环、每请求全量处理 Schema。本阶段先剖析再动手，只改**能本机复现、能当场测出前后差**的部分。

结论先放这里：**1k 表编排 P50 761 → 122 ms（6.2×），10k 表 8166 → 1307 ms（6.2×），10k 事件循环滞后 P95 3753 → 914 ms**。但吞吐仍不随并发增长——这一点本阶段推翻了阶段45 的一个假设，见第 5 节。

证据见 [stage-51-scale.json](results/stage-51-scale.json)。

## 1. 先定位：一次请求的时间到底花在哪

用 `cProfile` 抓一次 1k 表的 pipeline 请求，前 5 名是：

| 函数 | 累计耗时 | 调用次数 |
|---|---|---|
| `prompt_builder.py:521(<genexpr>)` | 3.95 s | **10 166 383 次迭代** |
| `str.isspace` / `str.isascii` | 0.77 s / 0.75 s | 各 10.8 M 次 |
| `copy.deepcopy` | 0.64 s | 358 168 次 |

也就是说，`estimate_tokens` 用 Python 逐字符遍历了整个上下文序列化结果——每次请求约 **1000 万字符**、2100 万次方法调用。这不是"慢了 10%"，而是每请求固定烧掉一大块 CPU，而且还跑在事件循环线程上。

改用 `node_event` 回调拿到各阶段耗时，确认了分布：

| 阶段 | 1k 表 | 说明 |
|---|---|---|
| parse | 1 ms | |
| retrieve | 121 ms | 关键词匹配 |
| generate | 49 ms | |
| **validate** | **491 ms** | 另一处全量处理，见第 2 节 |
| execute | 59 ms | |

## 2. 三处根因，三处改动

### (1) token 估算：逐字符 Python 循环 → 两次 C 级扫描

原实现：

```python
en_chars = sum(1 for c in text if c.isascii() and not c.isspace())
```

改为：

```python
ascii_text = text.encode("ascii", "ignore")
en_chars = len(ascii_text.translate(None, _ASCII_WHITESPACE))
```

`encode("ascii", "ignore")` 丢掉非 ASCII 字符，`translate(None, delete)` 一次删掉 ASCII 空白，两步都是 C 实现。**算术完全不变**：ASCII 空白集合按 `str.isspace()` 的语义取 `\t\n\v\f\r\x1c\x1d\x1e\x1f` 与空格（注意 Python 把 0x1c–0x1f 也算空白，这点容易漏），非 ASCII 空白照旧计入 CJK 一侧，`int(en/4 + zh/1.5)` 一字未改。

单函数基准：116 万字符 43 ms → 2 ms（与逐字符参考实现比 22×）。等价性用 300 条随机串（含 CJK、全角空格、emoji、控制字符）逐条比对，0 处不一致。

同一进程内 A/B（交替各 15 次请求，同一夹具）：**旧实现 P50 2183 ms，新实现 1152 ms**。

### (2) Schema 校验：为全部表建 sqlglot 映射 → 只为语句引用的表建

`check_schema_references` 原来把快照里**每一张表**都塞进 `MappingSchema`，并且每个列名都要 `to_identifier(...).sql(dialect=...)` 渲染一次。1k 表 × 12 列 = 每次校验 12003 次渲染、13015 次方言初始化——而语句只碰 1 张表。

改为先解析语句、取出真正引用的表名，再只为这些表建映射：

```python
expression = sqlglot.parse_one(sql, read=dialect)
referenced = {t.name for t in expression.find_all(exp.Table) if t.name}
```

两个坑，都踩到了：

- **`qualify` 会先规范化 AST 再回调**，所以 `on_qualify` 看到的 `table.name` 是小写规范化后的值（duckdb/sqlite 下 `MAIN.USERS` → `users`）。`sources` 的键必须用同样的规范化方式构造，否则命名空间校验拿不到源表，会把合法语句判成 `Qualified table namespace is not represented in source schema`。这是全量跑测试才暴露的——单独跑该文件时顺序不同，第一次没抓到。
- **大小写不敏感方言**（sqlite/duckdb）要能用 `USERS` 命中快照里的 `Users`，所以精确未命中时按 `casefold()` 再扫一遍。

校验强度不变：未知表仍然报 `Table 'x' not found in schema`，限定命名空间不匹配仍然失败，`parse_one` 失败仍然返回 `SCHEMA_ERROR`。1k 下 validate 阶段 **491 → 89 ms**，10k 下 710 → 415 ms。

### (3) 快照深拷贝：`copy.deepcopy` → 手写 `clone()`

`SchemaExtractor.cached_extract` 每次请求都要把缓存快照复制一份给调用方。`copy.deepcopy` 对每个 dataclass 实例走一遍 pickle 协议，10k 表快照实测 **1.3–2.7 s / 次**，成了该规模下单次查询最大的单项成本。

新增 `SchemaSnapshot.clone()` 与各级 `TableSchema/ColumnSchema/IndexInfo/ForeignKey.clone()`，按字段重建整棵树：

- 标量（str/int/float/bool/bytes/None）直接共享，其余叶子值仍走 `deepcopy`（不引入别名）；
- `created_at`、`duckdb_source_state` 是不可变对象，**故意共享**；
- 10k 表实测 **1352 → 377 ms（3.6×）**，`asdict(clone) == asdict(deepcopy)` 逐字段相等，改克隆不影响原对象（含嵌套 list/dict）。

没做的那一步：直接把缓存对象发出去、靠"大家都只读"维持正确性。省下的 377 ms 换不来一个可能被下游就地改坏缓存的隐患，不值。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `app/core/prompt_builder.py` | `estimate_tokens` 改 C 级扫描 + `_ASCII_WHITESPACE` 常量 |
| `app/core/schema_validation.py` | 只为语句引用的表建 `MappingSchema`；解析提前到构建之前 |
| `app/models/schema.py` | 新增 `clone()` 系列与 `_clone_leaf`，补 `from __future__ import annotations` |
| `app/db/schema_extractor.py` | 缓存命中与新建都改用 `snapshot.clone()`，移除 `copy` 导入 |

新增测试 26 项：

- `tests/test_core/test_token_estimation.py`（14 项）：以原逐字符实现为规格，参数化覆盖空白/控制字符/全角空格/emoji/纯 ASCII/纯 CJK 与 116 万字符的混合块，外加 40 ASCII=10 token、15 CJK=10 token 的锚点。
- `tests/test_db/test_schema_validation_scope.py`（6 项）：用计数器包住 `MappingSchema.add_table`，断言单表语句只加 1 张表、JOIN 只加 2 张；未知表、解析失败、大小写/命名空间行为不变。**这条在旧实现上失败**（旧实现会把 201 张表全加进去）。
- `tests/test_models/test_schema_clone.py`（5 项）：与 `deepcopy` 逐字段等价、克隆与原对象完全隔离（含嵌套结构）、只共享不可变字段、空快照、`cached_extract` 两次调用互不影响。

## 4. 前后对比（同一台机器，背靠背跑）

`scripts/scale_load`，`pipeline` 场景，scripted router 固定 SQL，mock embedding，单进程。改前/改后是同一份夹具规格、同一条命令，中间用 `git stash` 切换源码。

| 规模 | 指标 | 改前 | 改后 | 变化 |
|---|---|---|---|---|
| 1k 表（15 请求） | P50 | 761 ms | **122 ms** | 6.2× |
| | P95 | 893 ms | 241 ms | 3.7× |
| | rps | 1.25 | **6.86** | 5.5× |
| | 事件循环滞后 P95 | 379 ms | **68 ms** | 5.6× |
| 10k 表（6 请求） | P50 | 8166 ms | **1307 ms** | 6.2× |
| | P95 | 11061 ms | 1501 ms | 7.4× |
| | rps | 0.12 | **0.74** | 6.2× |
| | 事件循环滞后 P95 | 3753 ms | **914 ms** | 4.1× |

改前数字与阶段45 记录（1k 779 ms / 1.22 rps；10k 9781 ms / 0.10 rps）在同一量级，说明这不是"挑了个好日子"的对比。

## 5. 一个被推翻的假设：吞吐瓶颈不是 lease

阶段45 把"单连接 lease 串行"列为吞吐上限的第一候选。本阶段在 10k 表上做并发梯度（各 6 请求）：

| 并发 | rps | P50 |
|---|---|---|
| 1 | 0.31 | 3274 ms |
| 2 | 0.26 | 6647 ms |
| 4 | 0.32 | 12333 ms |

单请求 CPU 已经砍到原来的 1/6，**吞吐仍然不随并发增长，P50 仍随并发近似线性变长**。如果 lease 是唯一瓶颈，砍掉 80% 的单请求成本后吞吐应该明显上升——没有。原因在结构上：剩下的工作（快照克隆、关键词匹配、校验、上下文序列化）几乎全是单进程内的 Python CPU 工作，受 GIL 约束，多开协程只会排队。

结论改动：**lease 是串行的实现细节，不是吞吐上限的主因**。要让吞吐随核数增长，得把这些工作移出主进程（多进程/多 worker）或继续砍单请求 CPU；只做连接池不会有效果。这条修正写进阶段45 的后续说明，不修改它的历史记录。

## 6. 没有覆盖的地方

- **仍是合成 Schema + scripted router**：没有真实模型、真实 embedding，端到端语义延迟与 token 成本未测。这些数字是**编排容量**，不是线上 SLO。
- **只测了 SQLite**：PG/MySQL 的校验、内省路径没在本轮重测；大 schema 的 MySQL 场景另说。
- **100k 表未测**；10k 已经是"当前实现不适合在线查询"的档位，本轮把它从 8 秒压到 1.3 秒，但没有改变"10k 表不适合交互式"的结论边界。
- **多 worker / 多副本未设计未验收**：本阶段没有任何并发语义变化，`lease` 仍是一连接一槽。
- **上下文压缩（compaction）没动**：它把整个快照 `repr` 进 token 预算，在 10k 下必然超过 90% 阈值。本轮没有改它的语义（改了就动了"上下文算多少 token"的定义），这是一个**已知的测量口径问题**，留给后续单独设计。
- 关键词检索仍是 O(表数) 全扫（10k 下 retrieve 约 0.9 s），本阶段未做倒排索引/预计算子集。

## 7. 复现

```bash
# 改前 / 改后同一个命令，切源码用 git stash
python -m scripts.scale_load --output results/s51.json \
  --workdir .scale_probe/s51-1k --tables 1000 --documents 1000 \
  --requests 15 --concurrency 1 --warmup 1 --scenarios pipeline
```

`cProfile` 与阶段耗时的临时脚本没有进仓库（一次性诊断工具），复现靠上面这条命令 + `pytest -m 'not real_database'`。
